import sys

from SShExecutor import SShExecutor
from helpers.cloud_init import CloudInit
from helpers.ssh_key import SShKey
from config.config import Config
from metal_provision.provision import MetalProvision

from server_designer.designer import ServersDesigner
from server_designer.server import Server

config = Config()
AUTH_KEY = SShKey.BASE_DIR / "auth_key"

if AUTH_KEY.exists():
    with open(AUTH_KEY, "r") as f:
        priv = f.read()

    pub = SShKey.get_public_key_from_private(priv)
else:
    priv, pub = SShKey.generate_ssh_key()
    with open(AUTH_KEY, "w") as f:
        f.write(priv)

APP_USER = "userapp"
APP_HOME = f"/home/{APP_USER}"
cloud_init = CloudInit.render("av.rybin", pub, APP_USER)

provision = MetalProvision(
    s3_bucket=config.s3.bucket,
    s3_region=config.s3.region,
    s3_endpoint=config.s3.endpoint,
    passphrase=config.stand.passphrase,
    s3_access_key=config.s3.access_key,
    s3_secret_key=config.s3.secret_key,
    stand_name=config.stand.name,
    project_name=config.stand.project_name,
    user_name=config.stand.user,
    provider_token=config.hcloud.token,
)

designer = ServersDesigner(
    ssh_admin_name="AVRybin",
    user_data=cloud_init,
)

servers = {
    "test-server": Server(
        location="hel1",
        type="cpx32",
        image="357704314",
        network="network-p2p",
    )
}

program_for_provision = designer.get_program(servers)

if len(sys.argv) > 1 and sys.argv[1] == "destroy":
    provision.destroy(program_for_provision)
    sys.exit(0)

result = provision.create(program_for_provision)
ip_server = result.outputs.get("server_test-server_internal_ip")
print(ip_server.value)

pkey = SShKey.get_paramiko_key(priv)

executorSSH = SShExecutor(
    user="av.rybin",
    key=pkey,
    servers=[ip_server.value],
)

executorSSH.run(
    [
        SShExecutor.get_shell_command(
            name=f"Enable linger for {APP_USER}", user="", sudo=True, full_login=False,
            cmd=f"loginctl show-user {APP_USER} --property=Linger "
                f"| grep -q '^Linger=yes$' "
                f"|| loginctl enable-linger {APP_USER}",
        ),
        SShExecutor.get_shell_command(
            name=f"Create Podman config dirs for {APP_USER}", user="", sudo=True, full_login=False,
            cmd=f"install -d "
                f"-o {APP_USER} "
                f"-g $(id -gn {APP_USER}) "
                f"-m 700 "
                f"{APP_HOME}/.config "
                f"{APP_HOME}/.config/containers "
                f"{APP_HOME}/.config/containers/systemd",
        ),
        SShExecutor.get_shell_command(
            name="Create Podman network app-net", user=APP_USER, sudo=True, full_login=True,
            cmd="podman network exists app-net || podman network create app-net",
        ),
    ],
)
