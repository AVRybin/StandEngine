from dataclasses import dataclass, field, InitVar, replace
from pathlib import Path

from paramiko import PKey
from typing import Callable
from mako.template import Template
from box import Box

from InfraBaseLib import SShKey, CloudInit, MetalProvision, ServersDesigner, Server, SShExecutor
from InfraBaseLib.SShExecutor import InfraOperation, UploadFile
from ShellCollect import ShellCollect
from App import ClusterApp, App
from StandFramework import ConfigBackend, StandState


@dataclass(kw_only=True)
class Keys:
    private : str = ""

    pub : str = field(init=False)
    pkey: PKey = field(init=False)

    def __post_init__(self):
        if self.private == "":
            self.private, self.pub =  SShKey.generate_ssh_key()
        else:
            self.pub = SShKey.get_public_key_from_private(self.private)

        self.pkey = SShKey.get_paramiko_key(self.private)


@dataclass(kw_only=True)
class Node:
    location: InitVar[str]
    type_serv: InitVar[str]
    network: InitVar[str]
    image: InitVar[str]

    machine: Server = field(init=False)
    app_runtime: str
    roles_app: dict[str, list[str]] = field(default_factory=dict, init=False)
    instances_app: list[App]

    public_ip: str = field(init=False)
    private_ip: str = field(init=False)

    def __post_init__(self, location: str, type_serv: str, network: str, image: str):
        self.machine = Server(location=location, type=type_serv, network=network, image=image)


@dataclass(kw_only=True)
class InstanceApp:
    app: App
    cluster: ClusterApp = None
    node: Node = None


@dataclass(kw_only=True)
class Stand:
    private_key: InitVar[str] = ""
    key_name_admin: InitVar[str] = ""
    key: Keys = field(init=False)

    sudo_user: str
    app_user: str
    cloud_init: str = field(init=False)

    backend: ConfigBackend = None
    state: StandState
    path_folder_configset: Path
    
    provision: MetalProvision = field(init=False)
    void_provision: Callable[[], None] = field(init=False)
    inventory: dict[str, list[str]] = field(default_factory=dict, init=False)
    executor_shell: SShExecutor = field(default=None, init=False)

    nodes: dict[str, Node]
    shell_script: list[InfraOperation] = field(default_factory=list, init=False)

    clusters: InitVar[list[ClusterApp]] = None
    clusters_app: dict[str, ClusterApp] = field(default_factory=dict, init=False)
    instance_apps: dict[str, InstanceApp] = field(default_factory=dict, init=False)

    APP_RUNTIME: str = "podman"

    def __post_init__(self, private_key: str, key_name_admin: str, clusters: list[ClusterApp]):
        self.instance_apps = {}
        self.key = Keys(private=private_key)
        self.cloud_init = CloudInit.render(self.sudo_user, self.key.pub, self.app_user)

        if self.backend is None:
            self.backend = ConfigBackend()
        
        self.provision = MetalProvision(
            s3_bucket=self.backend.s3.bucket,
            s3_region=self.backend.s3.region,
            s3_endpoint=self.backend.s3.endpoint,
            passphrase=self.state.passphrase,
            s3_access_key=self.backend.s3.access_key,
            s3_secret_key=self.backend.s3.secret_key,
            stand_name=self.state.env,
            project_name=self.state.project,
            user_name=self.state.owner,
            provider_token=self.backend.hcloud.token,
        )

        designer = ServersDesigner(
            ssh_admin_name=key_name_admin,
            user_data=self.cloud_init,
        )

        servers_for_provision = {}
        for name, node in self.nodes.items():
            servers_for_provision[name] = node.machine

        self.void_provision = designer.get_program(servers_for_provision)

        self.clusters_app = {cluster.name: cluster for cluster in clusters}

        for cluster_name, cluster in self.clusters_app.items():
            for instance in cluster.instances_app:
                self.instance_apps[instance.name] = InstanceApp(
                    app = instance,
                    cluster = cluster,
                )

        for node_name, node in self.nodes.items():
            node.roles_app = {}

            for instance in node.instances_app:
                cluster = self.instance_apps[instance.name].cluster.name
                roles = node.roles_app.setdefault(cluster, [])

                if instance.role.name not in roles:
                    roles.append(instance.role.name)

                self.instance_apps[instance.name].node = node


    def destroy(self) -> None:
        self.provision.destroy(self.void_provision)

    def create_servers(self) -> None:
        result = self.provision.create(self.void_provision)

        for name, node in self.nodes.items():
            public_ip = result.outputs.get(f"server_{name}_public_ip")
            private_ip = result.outputs.get(f"server_{name}_internal_ip")

            if public_ip is not None:
                node.public_ip = public_ip.value
            else:
                raise Exception(f"Server {name} not found in outputs")

            if private_ip is not None:
                node.private_ip = private_ip.value
                keys_inv = private_ip.value
            else:
                raise Exception(f"Server {name} not found in outputs")

            self.inventory.setdefault(keys_inv, []).append(node.app_runtime)

            for app, roles in node.roles_app.items():
                self.inventory[keys_inv].append(app)
                for role in roles:
                    self.inventory[keys_inv].append(app + "___" + role)



        for _, instance in self.instance_apps.items():
            self.inventory[instance.node.private_ip].append(instance.cluster.name + "---" + instance.app.name)

        self.executor_shell = SShExecutor(user=self.sudo_user, key=self.key.pkey, server=self.inventory)

    def settings_runtime(self) -> None:
        self.shell_script.extend(ShellCollect.setting_podman_app_runtime(self.app_user, self.APP_RUNTIME))

    def add_app_install(self) -> None:
        for _, cluster in self.clusters_app.items():
            self.shell_script.extend(cluster.get_shell_install(self.app_user))

    def run_server_tasks(self) -> None:
        self.executor_shell.run(self.shell_script)

    def clear_shell_script(self) -> None:
        self.shell_script = []

    def render_deploy_configset(self) -> None:
        Path(self.path_folder_configset).mkdir(parents=True, exist_ok=True)

        for instance_name, instance in self.instance_apps.items():
            path = Path(self.path_folder_configset / f"{instance.cluster.name}--{instance_name}")
            Path(path).mkdir(parents=True, exist_ok=True)

            for configs_file in instance.cluster.paths_to_templates:
                template = Template(filename=str(configs_file.paths_to_templates))
                content = template.render(node=instance.node, instance=instance.app, role=instance.app.role,
                                      cluster=replace(instance.cluster, preferences=Box(instance.cluster.preferences)),
                                          apps=self.instance_apps)
                self.shell_script.append(UploadFile(
                    name="Upload config",
                    content=content,
                    dest=configs_file.dest,
                    for_group=instance.cluster.name + "---" + instance.app.name,
                    user=configs_file.owner,
                    mode=configs_file.mode,
                ))


                with open(path / configs_file.paths_to_templates.name.removesuffix('.mako'), "w") as f:
                    f.write(content)