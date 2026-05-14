import sys, os
from pathlib import Path

from config.config import Config

from StandFramework import Stand, StandState, Node
from ShellCollect import ShellCollect

config = Config()
AUTH_KEY = Path(__file__).parent / "auth_key"

key = ""
if AUTH_KEY.exists():
    with open(AUTH_KEY, "r") as f:
        key = f.read()

APP_RUNTIME = "podman"
stand_project = StandState(owner=config.stand.user, passphrase=config.stand.passphrase,
                           project=config.stand.project_name, env=config.stand.name)

servers = {
    "test-server": Node(
        location="hel1",
        type_serv="cpx32",
        image="357704314",
        network="network-p2p",
        app_runtime=APP_RUNTIME,
    )
}

stand = Stand(private_key=key, sudo_user="av.rybin", app_user="userapp", key_name_admin="AVRybin", nodes=servers,
              state=stand_project)


if len(sys.argv) > 1 and sys.argv[1] == "destroy":
    stand.destroy()
    os.remove(AUTH_KEY)
    sys.exit(0)

stand.create()

if not AUTH_KEY.exists():
    with open(AUTH_KEY, "w") as f:
        f.write(stand.key.private)

print(stand.nodes["test-server"].public_ip)
print(stand.nodes["test-server"].private_ip)

shell = ShellCollect.setting_podman_app_runtime(stand.app_user, APP_RUNTIME)
stand.executor_shell.run(shell)
