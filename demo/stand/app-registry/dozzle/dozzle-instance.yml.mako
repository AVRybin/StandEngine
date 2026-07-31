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
        - name: podman-socket
          mountPath: /var/run/docker.sock
          readOnly: true

        - name: dozzle-data
          mountPath: /data

      ports:
        - containerPort: 8080
          hostPort: 3000
          hostIP: ${node.private_ip}

      env:
        - name: DOZZLE_FILTER
          value: "label=stands-engine.io/managed=true"

  volumes:
    - name: podman-socket
      hostPath:
        path: /home/userapp/podman.sock

    - name: dozzle-data
      persistentVolumeClaim:
        claimName: dozzle-data
