#!/bin/bash

CONTAINER="${instance.name}"
MONGO_USER="${cluster.preferences.admin_user}"
MONGO_PASS="${cluster.preferences.admin_pass}"

podman exec -it "$CONTAINER" mongosh \
  -u "$MONGO_USER" \
  -p "$MONGO_PASS" \
  --authenticationDatabase admin \
  --eval 'rs.initiate({
    _id: "${cluster.preferences.replica_set_name}",
    members: [
      { _id: 0, host: "${node.private_ip}:27017" }
    ]
  })'

<%text>
DIR="./migration"
REMOTE_ROOT="/tmp/import"

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

  podman exec -it "$CONTAINER" mkdir -p "$remote_dir"
  podman cp "$file" "$CONTAINER:$remote_file"

  podman exec -it "$CONTAINER" sh -lc "
    mongoimport \
      --host=127.0.0.1 \
      --port=27017 \
      --username=\"$MONGO_USER\" \
      --password=\"$MONGO_PASS\" \
      --authenticationDatabase=admin \
      --db=\"$db\" \
      --collection=\"$collection\" \
      --file=\"$remote_file\" \
      --jsonArray \
      --mode=upsert
  "
done

podman exec -it "$CONTAINER" rm -rf "$REMOTE_ROOT" >/dev/null 2>&1 || true
</%text>
  