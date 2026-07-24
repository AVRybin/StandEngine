#!/bin/bash

RPK_USER="${cluster.preferences.admin_user}"
RPK_PASS="${cluster.preferences.admin_pass}"

RPK="podman exec ${instance.name} rpk"
BROKER_COUNT=${cluster.instance_count}
<%text>
AUTH="-X user=${RPK_USER} -X pass=${RPK_PASS} -X sasl.mechanism=PLAIN"

# ===== Config =====

DEFAULT_TOPIC_REPLICATION_FACTOR="${DEFAULT_TOPIC_REPLICATION_FACTOR:-$(( BROKER_COUNT < 3 ? BROKER_COUNT : 3 ))}"
if [[ -z "${DEFAULT_TOPIC_MIN_INSYNC_REPLICAS:-}" ]]; then
  DEFAULT_TOPIC_MIN_INSYNC_REPLICAS=1
  if [[ "$DEFAULT_TOPIC_REPLICATION_FACTOR" =~ ^[1-9][0-9]*$ ]] \
     && (( 10#$DEFAULT_TOPIC_REPLICATION_FACTOR > 1 )); then
    DEFAULT_TOPIC_MIN_INSYNC_REPLICAS=$(( 10#$DEFAULT_TOPIC_REPLICATION_FACTOR - 1 ))
  fi
fi

CONFIG_MAP_FILE="${CONFIG_MAP_FILE:-${ACL_MAP_FILE:-./acl-map.sh}}"
[[ -f "$CONFIG_MAP_FILE" ]] || { echo "Config map not found: $CONFIG_MAP_FILE"; exit 1; }
# shellcheck source=/dev/null
source "$CONFIG_MAP_FILE"
ACL_REPLACE="${ACL_REPLACE:-false}"

require_positive_integer() {
  local name="$1"
  local value="$2"
  if [[ ! "$value" =~ ^[1-9][0-9]*$ ]]; then
    echo "$name must be a positive integer, got: $value" >&2
    return 1
  fi
}

validate_cluster_config() {
  require_positive_integer "BROKER_COUNT" "$BROKER_COUNT" || return 1
  require_positive_integer \
    "DEFAULT_TOPIC_REPLICATION_FACTOR" \
    "$DEFAULT_TOPIC_REPLICATION_FACTOR" || return 1
  require_positive_integer \
    "DEFAULT_TOPIC_MIN_INSYNC_REPLICAS" \
    "$DEFAULT_TOPIC_MIN_INSYNC_REPLICAS" || return 1

  if (( 10#$DEFAULT_TOPIC_REPLICATION_FACTOR > 10#$BROKER_COUNT )); then
    echo "DEFAULT_TOPIC_REPLICATION_FACTOR cannot exceed BROKER_COUNT" >&2
    return 1
  fi
  if (( 10#$DEFAULT_TOPIC_MIN_INSYNC_REPLICAS > 10#$DEFAULT_TOPIC_REPLICATION_FACTOR )); then
    echo "DEFAULT_TOPIC_MIN_INSYNC_REPLICAS cannot exceed DEFAULT_TOPIC_REPLICATION_FACTOR" >&2
    return 1
  fi

  EXPECTED_BROKERS="${EXPECTED_BROKERS:-$BROKER_COUNT}"
  require_positive_integer "EXPECTED_BROKERS" "$EXPECTED_BROKERS" || return 1
  if (( 10#$EXPECTED_BROKERS > 10#$BROKER_COUNT )); then
    echo "EXPECTED_BROKERS cannot exceed BROKER_COUNT" >&2
    return 1
  fi

  local topic spec partitions replication retention min_isr extra
  for topic in "${!TOPICS[@]}"; do
    spec="${TOPICS[$topic]}"
    IFS=':' read -r partitions replication retention min_isr extra <<< "$spec"
    if [[ -n "$extra" || -z "$partitions" || -z "$retention" ]]; then
      echo "Topic $topic has invalid config format: $spec" >&2
      return 1
    fi
    require_positive_integer "Topic $topic replication" "$replication" || return 1
    require_positive_integer "Topic $topic min.insync.replicas" "$min_isr" || return 1
    if (( 10#$replication > 10#$BROKER_COUNT )); then
      echo "Topic $topic replication cannot exceed BROKER_COUNT" >&2
      return 1
    fi
    if (( 10#$replication > 10#$EXPECTED_BROKERS )); then
      echo "Topic $topic replication cannot exceed EXPECTED_BROKERS" >&2
      return 1
    fi
    if (( 10#$min_isr > 10#$replication )); then
      echo "Topic $topic min.insync.replicas cannot exceed replication" >&2
      return 1
    fi
  done
}

# How many rpk calls to run concurrently. Each call is mostly network wait,
# so 8 is a safe default; tune via env if needed.
MAX_PROCS="${MAX_PROCS:-8}"
# Set VERBOSE=true to run the final topic/user/acl listing.
VERBOSE="${VERBOSE:-false}"

# Failure flag shared across background jobs.
FAIL_FLAG="$(mktemp)"
cleanup() { rm -f "$FAIL_FLAG"; }
trap cleanup EXIT

mark_fail() { echo 1 >>"$FAIL_FLAG"; }
any_failed() { [[ -s "$FAIL_FLAG" ]]; }

# Block until a job slot frees up. Uses `wait -n` (bash 4.3+).
throttle() {
  while (( $(jobs -rp | wc -l) >= MAX_PROCS )); do
    wait -n 2>/dev/null || break
  done
}

is_exists_error() {
  local msg="$1"
  [[ "$msg" == *"_ALREADY_EXISTS"* ]] || \
  [[ "$msg" =~ [Aa]lready[[:space:]_-]*exists ]] || \
  [[ "$msg" =~ [Aa]lready[[:space:]]+been[[:space:]]+created ]]
}

run_or_skip_exists() {
  local label="$1"
  shift

  local output
  if output="$("$@" 2>&1)"; then
    echo "$label: created"
    return 0
  fi

  if is_exists_error "$output"; then
    echo "$label: already exists, skipping"
    return 0
  fi

  echo "$label: failed" >&2
  echo "$output" >&2
  return 1
}

is_not_found_error() {
  local msg="$1"
  [[ "$msg" == *"NOT_FOUND"* ]] || \
  [[ "$msg" =~ [Nn]ot[[:space:]_-]*found ]] || \
  [[ "$msg" =~ [Nn]o[[:space:]]+matching[[:space:]]+ACL ]] || \
  [[ "$msg" =~ [Dd]oes[[:space:]]+not[[:space:]]+exist ]]
}

delete_or_skip_missing() {
  local label="$1"
  shift

  local output
  if output="$("$@" -f 2>&1)"; then
    echo "$label: removed old entries"
    return 0
  fi

  if [[ "$output" == *"unknown shorthand flag"* ]] || \
     [[ "$output" == *"unknown flag"* ]] || \
     [[ "$output" == *"flag provided but not defined"* ]]; then
    if output="$(printf 'y\n' | "$@" 2>&1)"; then
      echo "$label: removed old entries"
      return 0
    fi
  fi

  if is_not_found_error "$output"; then
    echo "$label: no existing entries to remove"
    return 0
  fi

  echo "$label: delete failed" >&2
  echo "$output" >&2
  return 1
}

ensure_acl() {
  local kind="$1"
  local user="$2"
  local resource="$3"
  local operations="$4"
  local pattern="$5"
  local -a operation_flags=()
  local -a operation_list=()
  local op

  IFS=',' read -r -a operation_list <<< "$operations"
  for op in "${operation_list[@]}"; do
    operation_flags+=(--operation "$op")
  done

  if [[ "$kind" == "group" ]]; then
    if [[ "$ACL_REPLACE" == "true" ]]; then
      delete_or_skip_missing "ACL group User:$user:$resource:$operations:$pattern" \
        $RPK acl delete \
        --allow-principal "User:$user" \
        "${operation_flags[@]}" \
        --group "$resource" \
        --resource-pattern-type "$pattern" \
        $AUTH || return 1
    fi

    run_or_skip_exists "ACL group User:$user:$resource:$operations:$pattern" \
      $RPK acl create \
      --allow-principal "User:$user" \
      "${operation_flags[@]}" \
      --group "$resource" \
      --resource-pattern-type "$pattern" \
      $AUTH || return 1
    echo "ACL group User:$user:$resource:$operations:$pattern: ensured"
    return 0
  fi

  if [[ "$ACL_REPLACE" == "true" ]]; then
    delete_or_skip_missing "ACL topic User:$user:$resource:$operations:$pattern" \
      $RPK acl delete \
      --allow-principal "User:$user" \
      "${operation_flags[@]}" \
      --topic "$resource" \
      --resource-pattern-type "$pattern" \
      $AUTH || return 1
  fi

  run_or_skip_exists "ACL topic User:$user:$resource:$operations:$pattern" \
    $RPK acl create \
    --allow-principal "User:$user" \
    "${operation_flags[@]}" \
    --topic "$resource" \
    --resource-pattern-type "$pattern" \
    $AUTH || return 1
  echo "ACL topic User:$user:$resource:$operations:$pattern: ensured"
}

# --- Per-item wrappers so each can run as a background job ---

create_topic() {
  local topic="$1"
  local partitions replication retention min_isr
  IFS=':' read -r partitions replication retention min_isr <<< "${TOPICS[$topic]}"
  run_or_skip_exists "Topic $topic" $RPK topic create "$topic" \
    --partitions "$partitions" \
    --replicas "$replication" \
    --topic-config "retention.ms=$retention" \
    --topic-config "min.insync.replicas=$min_isr" \
    $AUTH || mark_fail
}

create_user() {
  local user="$1"
  echo "Creating user: $user"
  run_or_skip_exists "User $user" $RPK security user create "$user" \
    -p "${USERS[$user]}" \
    --mechanism SCRAM-SHA-256 \
    $AUTH || mark_fail
}

apply_group_rules() {
  local user="$1"
  local -a rules=()
  local rule resource pattern operations
  IFS=';' read -r -a rules <<< "${ACL_GROUP_RULES[$user]}"
  for rule in "${rules[@]}"; do
    IFS=':' read -r resource pattern operations <<< "$rule"
    echo "Setting group ACL for User:$user -> $resource ($pattern)"
    ensure_acl "group" "$user" "$resource" "$operations" "$pattern" || mark_fail
  done
}

apply_topic_rules() {
  local user="$1"
  local -a rules=()
  local rule resource pattern operations
  IFS=';' read -r -a rules <<< "${ACL_TOPIC_RULES[$user]}"
  for rule in "${rules[@]}"; do
    IFS=':' read -r resource pattern operations <<< "$rule"
    echo "Setting topic ACL for User:$user -> $resource ($pattern)"
    ensure_acl "topic" "$user" "$resource" "$operations" "$pattern" || mark_fail
  done
}

# ===== Wave 0 =====

READY_TIMEOUT="${READY_TIMEOUT:-120}"
READY_INTERVAL="${READY_INTERVAL:-2}"

wait_for_cluster() {
  local deadline=$(( SECONDS + READY_TIMEOUT ))
  local health controller node_ids brokers
  while (( SECONDS < deadline )); do
    health="$($RPK cluster health $AUTH 2>/dev/null)" || health=""

    controller="$(grep -iE 'Controller ID' <<<"$health" | grep -oE '\-?[0-9]+' | head -1)"

    # "All nodes:  [0 1 2]" -> вытащить содержимое скобок и посчитать id
    node_ids="$(grep -iE 'All nodes' <<<"$health" | sed -nE 's/.*\[([^]]*)\].*/\1/p')"
    if [[ -n "$node_ids" ]]; then
      brokers=$(wc -w <<<"$node_ids")
    else
      brokers=0
    fi

    if grep -qiE 'Healthy:[[:space:]]*true' <<<"$health" \
       && [[ -n "$controller" && "$controller" != "-1" ]] \
       && (( brokers >= EXPECTED_BROKERS )); then
      echo "Cluster ready: healthy, controller=$controller, brokers=$brokers"
      return 0
    fi
    echo "Waiting for cluster… (have brokers=$brokers, need $EXPECTED_BROKERS)"
    sleep "$READY_INTERVAL"
  done
  echo "Cluster not ready within ${READY_TIMEOUT}s (brokers=$brokers, controller=$controller)" >&2
  return 1
}

validate_cluster_config || exit 1
wait_for_cluster || exit 1

# ===== Wave 1: topics + users (independent of each other) =====

for topic in "${!TOPICS[@]}"; do
  throttle
  create_topic "$topic" &
done

for user in "${!USERS[@]}"; do
  throttle
  create_user "$user" &
done

wait
any_failed && { echo "Topic/user creation had failures" >&2; exit 1; }

# ===== Wave 2: ACLs =====

for user in "${!ACL_GROUP_RULES[@]}"; do
  throttle
  apply_group_rules "$user" &
done

for user in "${!ACL_TOPIC_RULES[@]}"; do
  throttle
  apply_topic_rules "$user" &
done

wait
any_failed && { echo "ACL creation had failures" >&2; exit 1; }

# ===== Verify (opt-in) =====

if [[ "$VERBOSE" == "true" ]]; then
  echo ""
  echo "=== Topics ==="
  $RPK topic list $AUTH

  echo ""
  echo "=== Users ==="
  $RPK security user list $AUTH

  echo ""
  echo "=== ACLs ==="
  $RPK acl list $AUTH
fi
</%text>
