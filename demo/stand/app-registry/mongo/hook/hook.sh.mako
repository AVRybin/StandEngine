#!/bin/bash

CONTAINER="${instance.name}"
MONGO_USER="${cluster.preferences.admin_user}"
MONGO_PASS="${cluster.preferences.admin_pass}"
MONGO_REPLICA_SET_NAME="${cluster.preferences.replica_set_name}"
MONGO_MEMBER_COUNT=${cluster.instance_count}
MONGO_MEMBER_HOSTS=(
% for mongo_instance in cluster.instances_app:
  "${apps[mongo_instance.name].node.private_ip}:27017"
% endfor
)

<%text>
MONGO_READY_TIMEOUT="${MONGO_READY_TIMEOUT:-120}"
MONGO_READY_INTERVAL="${MONGO_READY_INTERVAL:-2}"
DIR="./migration"
REMOTE_ROOT="/tmp/import"

if (( MONGO_MEMBER_COUNT < 1 || MONGO_MEMBER_COUNT > 7 )); then
  echo "Mongo replica set must contain between 1 and 7 voting members, got: $MONGO_MEMBER_COUNT" >&2
  exit 1
fi
if (( ${#MONGO_MEMBER_HOSTS[@]} != MONGO_MEMBER_COUNT )); then
  echo "Rendered Mongo member count does not match cluster.instance_count" >&2
  exit 1
fi

MONGO_MEMBER_HOSTS_CSV="$(IFS=,; echo "${MONGO_MEMBER_HOSTS[*]}")"

wait_for_members() {
  local deadline=$(( SECONDS + MONGO_READY_TIMEOUT ))
  local host all_ready

  while (( SECONDS < deadline )); do
    all_ready=true
    for host in "${MONGO_MEMBER_HOSTS[@]}"; do
      if ! podman exec "$CONTAINER" mongosh \
        --quiet \
        --host "$host" \
        --eval 'quit(db.adminCommand({ ping: 1 }).ok === 1 ? 0 : 1)' \
        >/dev/null 2>&1; then
        all_ready=false
        break
      fi
    done

    if [[ "$all_ready" == "true" ]]; then
      return 0
    fi
    sleep "$MONGO_READY_INTERVAL"
  done

  echo "Mongo members did not become reachable within ${MONGO_READY_TIMEOUT}s" >&2
  return 1
}

check_replica_set_config() {
  podman exec \
    --env "MONGO_RS_HOSTS=$MONGO_MEMBER_HOSTS_CSV" \
    "$CONTAINER" mongosh \
    --quiet \
    --host 127.0.0.1 \
    -u "$MONGO_USER" \
    -p "$MONGO_PASS" \
    --authenticationDatabase admin \
    --eval '
      const desired = process.env.MONGO_RS_HOSTS.split(",").sort();
      try {
        const actual = rs.conf().members.map(member => member.host).sort();
        if (JSON.stringify(actual) === JSON.stringify(desired)) {
          quit(0);
        }
        print("Replica set is already initialized with a different member list");
        quit(11);
      } catch (error) {
        if (error.code === 94 || error.codeName === "NotYetInitialized") {
          quit(10);
        }
        print(error);
        quit(12);
      }
    '
}

init_replica_set() {
  podman exec \
    --env "MONGO_RS_NAME=$MONGO_REPLICA_SET_NAME" \
    --env "MONGO_RS_HOSTS=$MONGO_MEMBER_HOSTS_CSV" \
    "$CONTAINER" mongosh \
    --quiet \
    --host 127.0.0.1 \
    -u "$MONGO_USER" \
    -p "$MONGO_PASS" \
    --authenticationDatabase admin \
    --eval '
      const hosts = process.env.MONGO_RS_HOSTS.split(",");
      const result = rs.initiate({
        _id: process.env.MONGO_RS_NAME,
        members: hosts.map((host, index) => ({ _id: index, host }))
      });
      if (result.ok !== 1) {
        printjson(result);
        quit(1);
      }
    '
}

wait_for_replica_set() {
  local deadline=$(( SECONDS + MONGO_READY_TIMEOUT ))
  local primary

  while (( SECONDS < deadline )); do
    primary="$(
      podman exec \
        --env "MONGO_RS_HOSTS=$MONGO_MEMBER_HOSTS_CSV" \
        "$CONTAINER" mongosh \
        --quiet \
        --host 127.0.0.1 \
        -u "$MONGO_USER" \
        -p "$MONGO_PASS" \
        --authenticationDatabase admin \
        --eval '
          try {
            const desired = process.env.MONGO_RS_HOSTS.split(",");
            const status = rs.status();
            const ready = status.members.filter(member =>
              desired.includes(member.name) &&
              (member.stateStr === "PRIMARY" || member.stateStr === "SECONDARY")
            );
            const primary = status.members.find(member => member.stateStr === "PRIMARY");
            if (primary && ready.length === desired.length) {
              print(primary.name);
              quit(0);
            }
          } catch (error) {
          }
          quit(1);
        ' 2>/dev/null
    )" && {
      PRIMARY_HOST="$(tail -n 1 <<< "$primary")"
      [[ -n "$PRIMARY_HOST" ]] && return 0
    }
    sleep "$MONGO_READY_INTERVAL"
  done

  echo "Mongo replica set did not become healthy within ${MONGO_READY_TIMEOUT}s" >&2
  return 1
}

wait_for_members || exit 1

check_replica_set_config
replica_set_status=$?
case "$replica_set_status" in
  0)
    echo "Mongo replica set already has the expected members"
    ;;
  10)
    init_replica_set || exit 1
    ;;
  11)
    echo "Automatic reconfiguration is disabled; reconfigure or recreate the replica set" >&2
    exit 1
    ;;
  *)
    echo "Failed to inspect Mongo replica set configuration" >&2
    exit 1
    ;;
esac

wait_for_replica_set || exit 1
echo "Mongo replica set ready; primary=$PRIMARY_HOST"

shopt -s nullglob
for file in "$DIR"/*.json; do
  base="$(basename "$file" .json)"

  db="${base%%.*}"
  collection="${base#*.}"

  if [[ "$db" == "$collection" ]]; then
    echo "Skip invalid file name: $file"
    continue
  fi

  remote_dir="$REMOTE_ROOT/$db"
  remote_file="$remote_dir/$collection.json"

  echo "Import $file -> $db.$collection"

  podman exec "$CONTAINER" mkdir -p "$remote_dir"
  podman cp "$file" "$CONTAINER:$remote_file"

  podman exec "$CONTAINER" mongoimport \
    --host="$PRIMARY_HOST" \
    --username="$MONGO_USER" \
    --password="$MONGO_PASS" \
    --authenticationDatabase=admin \
    --db="$db" \
    --collection="$collection" \
    --file="$remote_file" \
    --jsonArray \
    --mode=upsert || exit 1
done

podman exec "$CONTAINER" rm -rf "$REMOTE_ROOT" >/dev/null 2>&1 || true
</%text>
