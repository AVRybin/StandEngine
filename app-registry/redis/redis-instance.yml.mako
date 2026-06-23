apiVersion: v1
kind: Secret
metadata:
  name: redis-credentials
type: Opaque
stringData:
  REDIS_USER: "${cluster.preferences.admin_user}"
  REDIS_PASSWORD: "${cluster.preferences.admin_pass}"

---

apiVersion: v1
kind: ConfigMap
metadata:
  name: redis-config
data:
  redis.conf: |
    bind 0.0.0.0
    port 6379
    
    protected-mode yes
    
    dir /data
    
    appendonly yes
    appendfilename "appendonly.aof"
    appendfsync everysec
    
    save 900 1
    save 300 10
    save 60 10000
    
    aclfile /run/redis/users.acl
    
    tcp-keepalive 300
    timeout 0
    
    maxmemory-policy noeviction

---

apiVersion: v1
kind: Pod
metadata:
  name: ${instance.name}
  annotations:
    ad.datadoghq.com/my-app.logs: '[{"source": "infra", "service": "${instance.name}"}]'
spec:
  restartPolicy: Always

  containers:
    - name: ${instance.name}
      image: ${cluster.image.registry}/${cluster.image.path}:${cluster.image.version}

      resources:
        requests:
          cpu: "1"
          memory: "2Gi"
        limits:
          cpu: "1"
          memory: "2Gi"

      volumeMounts:
        - name: redis-storage
          mountPath: /data

        - name: redis-config
          mountPath: /usr/local/etc/redis/redis.conf
          subPath: redis.conf

        - name: redis-acl
          mountPath: /run/redis

      ports:
        - containerPort: 6379
          hostPort: 6379
          hostIP: ${node.private_ip}

      env:
        - name: REDIS_USER
          valueFrom:
            secretKeyRef:
              name: redis-credentials
              key: REDIS_USER

        - name: REDIS_PASSWORD
          valueFrom:
            secretKeyRef:
              name: redis-credentials
              key: REDIS_PASSWORD

      command:
        - /bin/sh

      args:
        - -ec
        - |
          printf 'user default off\nuser %s on >%s ~* &* +@all\n' "$REDIS_USER" "$REDIS_PASSWORD" > /run/redis/users.acl
          
          exec redis-server /usr/local/etc/redis/redis.conf \
          % if role.name == 'replica':
            --replicaof ${apps['redis-master'].node.private_ip} 6379 \
            --masteruser "$REDIS_USER" \
            --masterauth "$REDIS_PASSWORD" \
          % endif

  volumes:
    - name: redis-storage
      persistentVolumeClaim:
        claimName: redis-storage

    - name: redis-config
      configMap:
        name: redis-config

    - name: redis-acl
      emptyDir:
        medium: Memory

