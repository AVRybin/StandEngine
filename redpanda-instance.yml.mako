apiVersion: v1
kind: Secret
metadata:
  name: redpanda-credentials
type: Opaque
stringData:
  SUPER_USER: "${cluster.preferences.admin_user}:${cluster.preferences.admin_pass}"

---

apiVersion: v1
kind: ConfigMap
metadata:
  name: redpanda-bootstrap
data:
  bootstrap.yaml: |
    enable_sasl: true
    superusers:
      - ${cluster.preferences.admin_user}
    sasl_mechanisms:
      - SCRAM
      - PLAIN

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
          cpu: "3"
          memory: "6Gi"
        limits:
          cpu: "3"
          memory: "6Gi"
      volumeMounts:
        - name: kafka-storage
          mountPath: /var/lib/redpanda/data
        - name: bootstrap-config
          mountPath: /etc/redpanda/.bootstrap.yaml
          subPath: bootstrap.yaml
      ports:
        - containerPort: 19092
          hostPort: 19092
          hostIP: ${node.private_ip}
        - containerPort: 33145
          hostPort: 33145
          hostIP: ${node.private_ip}
        % if role.name == 'first-seed':
        - containerPort: 9644
          hostPort: 9644
          hostIP: ${node.private_ip}
        % endif
      env:
        - name: RP_BOOTSTRAP_USER
          valueFrom:
            secretKeyRef:
              name: redpanda-credentials
              key: SUPER_USER
      command:
        - /usr/bin/rpk
      args:
        - redpanda
        - start
        - --kafka-addr
        - 0.0.0.0:19092
        - --advertise-kafka-addr
        - ${node.private_ip}:19092
        - --rpc-addr
        - 0.0.0.0:33145
        - --advertise-rpc-addr
        - ${node.private_ip}:33145
        % if role.name == 'base-seed':
        - --seeds
        - ${node.private_ip}:33145
        % endif

  volumes:
    - name: kafka-storage
      persistentVolumeClaim:
        claimName: kafka-storage
    - name: bootstrap-config
      configMap:
        name: redpanda-bootstrap
