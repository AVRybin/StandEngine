#!/bin/bash

RPK_USER="${cluster.preferences.admin_user}"
RPK_PASS="${cluster.preferences.admin_pass}"

RPK="podman exec ${instance.name}-${instance.name} rpk"
<%text>
AUTH="-X user=${RPK_USER} -X pass=${RPK_PASS} -X sasl.mechanism=PLAIN"
# ===== Config =====

CONFIG_MAP_FILE="${CONFIG_MAP_FILE:-${ACL_MAP_FILE:-./acl-map.sh}}"
[[ -f "$CONFIG_MAP_FILE" ]] || { echo "Config map not found: $CONFIG_MAP_FILE"; exit 1; }
# shellcheck source=/dev/null
source "$CONFIG_MAP_FILE"
ACL_REPLACE="${ACL_REPLACE:-false}"

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

# ===== Topics =====

for topic in "${!TOPICS[@]}"; do
  IFS=':' read -r partitions replication retention min_isr <<< "${TOPICS[$topic]}"
  run_or_skip_exists "Topic $topic" $RPK topic create "$topic" \
    --partitions "$partitions" \
    --replicas "$replication" \
    --topic-config "retention.ms=$retention" \
    --topic-config "min.insync.replicas=$min_isr" \
    $AUTH || exit 1
done

# ===== Users =====

for user in "${!USERS[@]}"; do
  echo "Creating user: $user"
  run_or_skip_exists "User $user" $RPK security user create "$user" \
    -p "${USERS[$user]}" \
    --mechanism SCRAM-SHA-256 \
    $AUTH || exit 1
done

# ===== ACLs =====

for user in "${!ACL_GROUP_RULES[@]}"; do
  IFS=';' read -r -a rules <<< "${ACL_GROUP_RULES[$user]}"
  for rule in "${rules[@]}"; do
    IFS=':' read -r resource pattern operations <<< "$rule"
    echo "Setting group ACL for User:$user -> $resource ($pattern)"
    ensure_acl "group" "$user" "$resource" "$operations" "$pattern" || exit 1
  done
done

for user in "${!ACL_TOPIC_RULES[@]}"; do
  IFS=';' read -r -a rules <<< "${ACL_TOPIC_RULES[$user]}"
  for rule in "${rules[@]}"; do
    IFS=':' read -r resource pattern operations <<< "$rule"
    echo "Setting topic ACL for User:$user -> $resource ($pattern)"
    ensure_acl "topic" "$user" "$resource" "$operations" "$pattern" || exit 1
  done
done

# ===== Verify =====

echo ""
echo "=== Topics ==="
$RPK topic list $AUTH

echo ""
echo "=== Users ==="
$RPK security user list $AUTH

echo ""
echo "=== ACLs ==="
$RPK acl list $AUTH
</%text>