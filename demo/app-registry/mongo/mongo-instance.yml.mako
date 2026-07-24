apiVersion: v1
kind: Secret
metadata:
  name: mongodb-credentials
type: Opaque
stringData:
    MONGO_INITDB_ROOT_USERNAME: "${cluster.preferences.admin_user}"
    MONGO_INITDB_ROOT_PASSWORD: "${cluster.preferences.admin_pass}"
  % if role.name in ['first-seed', 'member']:
    MONGO_REPLICA_SET_KEY: "${cluster.preferences.replica_set_key}"
  % endif

---

apiVersion: v1
kind: ConfigMap
metadata:
  name: mongodb-config
data:
  mongod.conf: |
    storage:
      dbPath: /data/db

    net:
      bindIp: 0.0.0.0
      port: 27017

    processManagement:
      timeZoneInfo: /usr/share/zoneinfo

---

apiVersion: v1
kind: Pod
metadata:
  name: ${instance.name}
  labels:
    stands-engine.io/managed: "true"
  annotations:
    ad.datadoghq.com/my-app.logs: '[{"source": "infra", "service": "${instance.name}"}]'
    % if instance.oom_priority is not None:
    io.podman.annotations.oom_score_adj: "${instance.oom_priority}"
    % endif
spec:
  restartPolicy: Always

  containers:
    - name: ${instance.name}
      image: ${cluster.image.full_name}

      resources:
        requests:
          cpu: "${instance.cpu}m"
          memory: "${instance.ram}M"
        limits:
          cpu: "${instance.cpu}m"
          memory: "${instance.ram}M"

      volumeMounts:
          - name: mongo-storage
            mountPath: /data/db

          - name: mongo-config
            mountPath: /etc/mongod.conf
            subPath: mongod.conf

        % if role.name in ['first-seed', 'member']:
          - name: mongo-keyfile
            mountPath: /run/mongodb-keyfile
        % endif

      ports:
        - containerPort: 27017
          hostPort: 27017
          hostIP: ${node.private_ip}

      env:
          - name: MONGO_INITDB_ROOT_USERNAME
            valueFrom:
              secretKeyRef:
                name: mongodb-credentials
                key: MONGO_INITDB_ROOT_USERNAME

          - name: MONGO_INITDB_ROOT_PASSWORD
            valueFrom:
              secretKeyRef:
                name: mongodb-credentials
                key: MONGO_INITDB_ROOT_PASSWORD

        % if role.name in ['first-seed', 'member']:
          - name: MONGO_REPLICA_SET_KEY
            valueFrom:
                secretKeyRef:
                  name: mongodb-credentials
                  key: MONGO_REPLICA_SET_KEY

          - name: MONGO_REPLICA_SET_NAME
            value: "${cluster.preferences.replica_set_name}"
        % endif

      command:
        - /bin/sh

      args:
        - -ec
        - |
          set -- mongod --config /etc/mongod.conf

          % if role.name in ['first-seed', 'member']:
          install -d /run/mongodb-keyfile
          printf '%s\n' "$MONGO_REPLICA_SET_KEY" > /run/mongodb-keyfile/keyfile

          chown -R mongodb:mongodb /run/mongodb-keyfile
          chmod 0700 /run/mongodb-keyfile
          chmod 0400 /run/mongodb-keyfile/keyfile

          set -- "$@" \
            --replSet "$MONGO_REPLICA_SET_NAME" \
            --keyFile /run/mongodb-keyfile/keyfile \
            --setParameter disableSplitHorizonIPCheck=true
          % endif

          exec /usr/local/bin/docker-entrypoint.sh "$@"

  volumes:
      - name: mongo-storage
        persistentVolumeClaim:
          claimName: mongo-storage

      - name: mongo-config
        configMap:
          name: mongodb-config

    % if role.name in ['first-seed', 'member']:
      - name: mongo-keyfile
        emptyDir:
          medium: Memory
    % endif
