import sys, os
from pathlib import Path
from dataclasses import replace

from config.config import Config

from StandFramework import Stand, StandState, Node
from ShellCollect import Port, Image
from App import App, RoleApp, ClusterApp, ConfigFile

config = Config()
AUTH_KEY = Path(__file__).parent / "auth_key"

key = ""
if AUTH_KEY.exists():
    with open(AUTH_KEY, "r") as f:
        key = f.read()

APP_RUNTIME = "podman"
portBase = Port(
    number=19092,
    protocol="tcp",
    zone="internal",
)

role_master = RoleApp(name="first-seed",
                      ports=[portBase, replace(portBase, number=33145), replace(portBase, number=9644)])
role_seed = RoleApp(name="base-seed",
                      ports=[portBase, replace(portBase, number=33145)])
instance_master = App(role=role_master, name="redpanda-master")
instance_seed_1 = App(role=role_seed, name="redpanda-seed-1")
instance_seed_2 = App(role=role_seed, name="redpanda-seed-2")

redpanda = ClusterApp(
    name="redpanda",
    image=Image(
        path="redpandadata/redpanda",
        version="v25.3.7",
        registry="docker.io"
    ),
    instances_app=[instance_master, instance_seed_1, instance_seed_2],
    preferences={"admin_pass": "tempPassword6512", "admin_user": "cool_admin"},
    paths_to_templates=[ConfigFile(
        paths_to_templates=Path(__file__).parent / "redpanda-instance.yml.mako",
        dest="/home/userapp/redpanda-instance.yml",
        owner="userapp",
        mode="644")],
)

stand_project = StandState(owner=config.stand.user, passphrase=config.stand.passphrase,
                           project=config.stand.project_name, env=config.stand.name)

baseServer = dict(
    location="hel1",
    type_serv="cpx32",
    image="357704314",
    network="network-p2p",
    app_runtime=APP_RUNTIME,
)

servers = {
    "master-server": Node(**baseServer, instances_app=[instance_master]),
    "seed-server-1": Node(**baseServer, instances_app=[instance_seed_1]),
    "seed-server-2": Node(**baseServer, instances_app=[instance_seed_2]),
}

stand = Stand(private_key=key, sudo_user="av.rybin", app_user="userapp", key_name_admin="AVRybin", nodes=servers,
              state=stand_project, clusters=[redpanda], path_folder_configset=Path(__file__).parent / "configset")


if len(sys.argv) > 1 and sys.argv[1] == "destroy":
    stand.destroy()
    os.remove(AUTH_KEY)
    sys.exit(0)

stand.create_servers()

if not AUTH_KEY.exists():
    with open(AUTH_KEY, "w") as f:
        f.write(stand.key.private)

print(stand.nodes["master-server"].public_ip)
print(stand.nodes["master-server"].private_ip)

stand.render_deploy_configset()
stand.settings_runtime()
stand.add_app_install()
stand.run_server_tasks()
