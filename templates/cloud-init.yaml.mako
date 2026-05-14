#cloud-config
users:
  - name: ${user_admin}
    groups: admins
    shell: /bin/bash
    sudo: ALL=(ALL) NOPASSWD:ALL
    ssh_authorized_keys:
      - ${ssh_public_key}

  - name: ${user_app}
    groups: apps
    shell: /bin/bash
