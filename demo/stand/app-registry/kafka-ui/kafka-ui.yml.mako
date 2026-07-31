apiVersion: v1
kind: ConfigMap
metadata:
  name: kafka-ui-config
data:
  dynamic_config.yaml: |
    kafka:
      clusters:
        - name: my-cluster
          bootstrapServers: ${apps['redpanda-master'].node.private_ip}:19092
          properties:
            security.protocol: SASL_PLAINTEXT
            sasl.mechanism: PLAIN
            sasl.jaas.config: >-
              org.apache.kafka.common.security.plain.PlainLoginModule required
              username="${'${KAFKA_USER}'}"
              password="${'${KAFKA_PASSWORD}'}";
    auth:
      type: LOGIN_FORM
    spring:
      security:
        user:
          name: ${cluster.preferences.admin_user}
          password: "{bcrypt}${cluster.preferences.admin_pass_bcrypt}"

---

apiVersion: v1
kind: Secret
metadata:
  name: redpanda-credentials
type: Opaque
stringData:
  SUPER_USER_PASSWORD: "${apps['redpanda-master'].cluster.preferences['admin_pass']}"

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
  restartPolicy: OnFailure
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
        - name: config-volume
          mountPath: /etc/kafkaui/dynamic_config.yaml
          subPath: dynamic_config.yaml
      ports:
        - containerPort: 8080
          hostPort: 8180
          hostIP: ${node.private_ip}
          name: console
      env:
        - name: DYNAMIC_CONFIG_ENABLED
          value: "false"
        - name: SPRING_CONFIG_ADDITIONAL_LOCATION
          value: /etc/kafkaui/dynamic_config.yaml
        - name: KAFKA_USER
          value: "${apps['redpanda-master'].cluster.preferences['admin_user']}"
        - name: KAFKA_PASSWORD
          valueFrom:
            secretKeyRef:
              name: redpanda-credentials
              key: SUPER_USER_PASSWORD
  volumes:
    - name: config-volume
      configMap:
        name: kafka-ui-config
