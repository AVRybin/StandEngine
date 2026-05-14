from dataclasses import dataclass, field, InitVar
from paramiko import PKey
from typing import Callable

from InfraBaseLib import SShKey, CloudInit, MetalProvision, ServersDesigner, Server, SShExecutor, ShellCommand
from ShellCollect import ShellCollect
from App import App
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
    apps: dict[str, list[str]] = field(default_factory=dict)

    public_ip: str = field(init=False)
    private_ip: str = field(init=False)

    def __post_init__(self, location: str, type_serv: str, network: str, image: str):
        self.machine = Server(location=location, type=type_serv, network=network, image=image)




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
    
    provision: MetalProvision = field(init=False)
    void_provision: Callable[[], None] = field(init=False)
    inventory: dict[str, list[str]] = field(default_factory=dict, init=False)
    executor_shell: SShExecutor = field(default=None, init=False)

    nodes: dict[str, Node]
    shell_script: list[ShellCommand] = field(default_factory=list, init=False)

    APP_RUNTIME: str = "podman"

    def __post_init__(self, private_key: str, key_name_admin: str):
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


    def destroy(self) -> None:
        self.provision.destroy(self.void_provision)

    def create_servers(self) -> None:
        result = self.provision.create(self.void_provision)
        keys_inv = ""

        for name, node in self.nodes.items():
            public_ip = result.outputs.get(f"server_{name}_public_ip")
            private_ip = result.outputs.get(f"server_{name}_internal_ip")

            if public_ip is not None:
                self.nodes[name].public_ip = public_ip.value

            if private_ip is not None:
                self.nodes[name].private_ip = private_ip.value
                keys_inv = private_ip.value

            self.inventory.setdefault(keys_inv, []).append(self.nodes[name].app_runtime)

            for app, roles in self.nodes[name].apps.items():
                self.inventory[keys_inv].append(app)
                for role in roles:
                    self.inventory[keys_inv].append(app + "___" + role)

        self.executor_shell = SShExecutor(user=self.sudo_user, key=self.key.pkey, server=self.inventory)

    def settings_runtime(self) -> None:
        self.shell_script.extend(ShellCollect.setting_podman_app_runtime(self.app_user, self.APP_RUNTIME))

    def add_app_install(self, app: App) -> None:
        self.shell_script.extend(app.get_shell_install(self.app_user))

    def run_server_tasks(self) -> None:
        self.executor_shell.run(self.shell_script)