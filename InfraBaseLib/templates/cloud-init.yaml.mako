#cloud-config

groups:
  - admins
  - apps

users:
  - name: ${user_admin}
    primary_group: admins
    shell: /bin/bash
    lock_passwd: true
    ssh_authorized_keys:
      - ${ssh_public_key}

  - name: ${user_app}
    primary_group: apps
    shell: /bin/bash
    lock_passwd: true

write_files:
  - path: /etc/sudoers.d/admins
    permissions: "0440"
    content: |
      %%admins ALL=(ALL:ALL) NOPASSWD:ALL

  - path: /etc/ssh/sshd_config.d/99-hardening.conf
    permissions: "0644"
    content: |
      PermitRootLogin no
      PasswordAuthentication no
      PermitEmptyPasswords no
      PubkeyAuthentication yes
      X11Forwarding no
      MaxAuthTries 3

      KexAlgorithms sntrup761x25519-sha512@openssh.com,curve25519-sha256,curve25519-sha256@libssh.org,diffie-hellman-group16-sha512,diffie-hellman-group18-sha512
      Ciphers chacha20-poly1305@openssh.com,aes256-gcm@openssh.com,aes128-gcm@openssh.com,aes256-ctr,aes192-ctr,aes128-ctr
      MACs hmac-sha2-512-etm@openssh.com,hmac-sha2-256-etm@openssh.com,umac-128-etm@openssh.com

disable_root: true
ssh_pwauth: false

packages:
  - firewalld
  - podman

runcmd:
  - firewall-offline-cmd --new-zone=public || true
  - firewall-offline-cmd --new-zone=internal || true
  - firewall-offline-cmd --zone=public --set-target=DROP
  - firewall-offline-cmd --zone=internal --set-target=DROP
  - firewall-offline-cmd --set-default-zone=public
  - firewall-offline-cmd --zone=internal --add-source=10.1.0.0/16
  - firewall-offline-cmd --zone=internal --add-service=ssh
  - systemctl enable --now firewalld
  - sshd -t && systemctl restart ssh 2>/dev/null || systemctl restart sshd

  - >
    wget --connect-timeout=10 --read-timeout=15 --tries=5 --waitretry=5 --retry-connrefused -c
    https://hel1.your-objectstorage.com/file-upload/podlet/0.3.2/podlet-x86_64-unknown-linux-gnu.tar.xz
  - tar -xJf podlet-x86_64-unknown-linux-gnu.tar.xz
  - mv ./podlet-x86_64-unknown-linux-gnu/podlet /usr/bin/podlet
  - rm -rf podlet-x86_64-unknown-linux-gnu*
