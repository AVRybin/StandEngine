from dataclasses import dataclass, field, InitVar, replace
from pathlib import Path
import re

from paramiko import PKey
from typing import Callable
from mako.template import Template
from box import Box

from InfraBaseLib import SShKey, CloudInit, MetalProvision, ServersDesigner, Server, SShExecutor, ShellCommand
from InfraBaseLib.SShExecutor import InfraOperation, UploadAsset, SShExecutorDiagnostArgs
from ShellCollect import ShellCollect, Port, Image, ImageRegistry
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
    node_groups: dict[str, str] = field(default_factory=dict, init=False)
    executor_shell: SShExecutor = field(default=None, init=False)

    nodes: dict[str, Node]
    shell_script: list[InfraOperation] = field(default_factory=list, init=False)

    clusters: InitVar[list[ClusterApp]] = None
    clusters_app: dict[str, ClusterApp] = field(default_factory=dict, init=False)
    instance_apps: dict[str, InstanceApp] = field(default_factory=dict, init=False)
    _registries: dict[str, ImageRegistry] = field(default_factory=dict, init=False)

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

        self.clusters_app = {cluster.name: cluster for cluster in clusters}

        for cluster_name, cluster in self.clusters_app.items():
            registry = cluster.image.registry
            current_registry = self._registries.get(registry.url)
            if current_registry is not None and current_registry != registry:
                raise ValueError(f"Registry {registry.url} has conflicting configuration")

            self._registries[registry.url] = registry

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

            node.machine.labels = self.build_node_labels(node)

        designer = ServersDesigner(
            ssh_admin_name=key_name_admin,
            user_data=self.cloud_init,
        )

        servers_for_provision = {}
        for name, node in self.nodes.items():
            servers_for_provision[name] = node.machine

        self.void_provision = designer.get_program(servers_for_provision)

    def build_node_labels(self, node: Node) -> dict[str, str]:
        labels = {
            "stand_name": self.sanitize_label_value(self.state.env),
            "stand_owner": self.sanitize_label_value(self.state.owner),
            "project": self.sanitize_label_value(self.state.project),
        }

        for instance in node.instances_app:
            labels[self.sanitize_label_key(instance.name)] = ""

        return labels

    @staticmethod
    def sanitize_label_value(value: str) -> str:
        normalized = re.sub(r"[^A-Za-z0-9_.-]+", "-", str(value)).strip("_.-")
        return normalized or "unknown"

    @classmethod
    def sanitize_label_key(cls, value: str) -> str:
        return cls.sanitize_label_value(value).lower()


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

            node_group = f"node---{name}"
            self.node_groups[name] = node_group
            self.inventory.setdefault(keys_inv, []).append(node.app_runtime)
            self.inventory[keys_inv].append(node_group)

            for app, roles in node.roles_app.items():
                self.inventory[keys_inv].append(app)
                for role in roles:
                    self.inventory[keys_inv].append(app + "___" + role)



        for _, instance in self.instance_apps.items():
            self.inventory[instance.node.private_ip].append(instance.cluster.name + "---" + instance.app.name)

        self.executor_shell = SShExecutor(user=self.sudo_user, key=self.key.pkey, server=self.inventory)

    def settings_runtime(self) -> None:
        self.shell_script.extend(ShellCollect.setting_podman_app_runtime(self.app_user, self.APP_RUNTIME))

    def get_node_shell_install_apps(self, node: Node, node_group: str) -> list[InfraOperation]:
        shell = []
        ports: dict[tuple[int, str, str], Port] = {}
        images: dict[str, Image] = {}
        registries: dict[str, ImageRegistry] = {}

        for instance in node.instances_app:
            for port in instance.role.ports:
                ports[(port.number, port.protocol, port.zone)] = port

            cluster = self.instance_apps[instance.name].cluster
            images[cluster.image.full_name] = cluster.image
            registries[cluster.image.registry.url] = self._registries[cluster.image.registry.url]

        shell.extend(ShellCollect.open_ports(list(ports.values()), node_group))

        node_images = list(images.values())
        node_registries = list(registries.values())
        login_command = ShellCollect.login_registries(node_registries, self.app_user, node_group)
        if login_command is not None:
            shell.append(login_command)

        download_command = ShellCollect.download_images(node_images, self.app_user, node_group)
        if download_command is not None:
            shell.append(download_command)

        logout_command = ShellCollect.logout_registries(node_registries, self.app_user, node_group)
        if logout_command is not None:
            shell.append(logout_command)

        return shell

    def add_app_install(self) -> None:
        for node_name, node in self.nodes.items():
            self.shell_script.extend(self.get_node_shell_install_apps(node, self.node_groups[node_name]))

    def build_preflight_operations(self) -> list[InfraOperation]:
        return [
            ShellCollect.wait_cloud_init(self.APP_RUNTIME),
        ]

    def run_server_tasks(self, diagnostic: bool | SShExecutorDiagnostArgs = False) -> None:
        self.executor_shell.run(
            self.shell_script,
            diagnostic=diagnostic,
            app_user=self.app_user,
            preflight_operations=self.build_preflight_operations(),
        )

    def clear_shell_script(self) -> None:
        self.shell_script = []
        if self.executor_shell is not None:
            self.executor_shell.clear_upload_files()

    def node_group_for_instance(self, instance: InstanceApp) -> str:
        for node_name, node in self.nodes.items():
            if node is instance.node:
                return self.node_groups[node_name]

        raise ValueError(f"Node group not found for instance {instance.app.name}")

    def add_upload_asset(self, instance: InstanceApp, asset: UploadAsset) -> None:
        if self.executor_shell is None:
            raise RuntimeError("create_servers must be called before adding upload assets")

        self.executor_shell.add_upload_asset(self.node_group_for_instance(instance), asset)

    def render_app_template(self, template_path: Path, instance: InstanceApp) -> str:
        template = Template(filename=str(template_path))
        return template.render(
            node=instance.node,
            instance=instance.app,
            role=instance.app.role,
            cluster=replace(instance.cluster, preferences=Box(instance.cluster.preferences)),
            apps=self.instance_apps,
        )

    def render_deploy_configset(self) -> None:
        Path(self.path_folder_configset).mkdir(parents=True, exist_ok=True)

        for instance_name, instance in self.instance_apps.items():
            path = Path(self.path_folder_configset / f"{instance.cluster.name}--{instance_name}")
            Path(path).mkdir(parents=True, exist_ok=True)

            for _,configs_file in instance.cluster.paths_to_templates.items():
                content = self.render_app_template(configs_file.paths_to_templates, instance)
                self.add_upload_asset(instance, UploadAsset(
                    content=content,
                    dest=configs_file.dest,
                    owner=configs_file.owner,
                    mode=configs_file.mode,
                ))


                with open(path / configs_file.paths_to_templates.name.removesuffix('.mako'), "w") as f:
                    f.write(content)

    def add_app_hook(self, instance: InstanceApp) -> None:
        if instance.app.hook_path is None:
            return

        hook_path = Path(instance.app.hook_path)
        if not hook_path.is_dir():
            raise Exception(f"Hook path is not a directory: {hook_path}")

        hook_sh = hook_path / "hook.sh.mako"
        if not hook_sh.is_file():
            raise Exception(f"Hook path must contain hook.sh.mako: {hook_sh}")

        for_group = instance.cluster.name + "---" + instance.app.name
        remote_hook_dir = f"/home/{self.app_user}/hook/{instance.app.name}"
        local_hook_dir = Path(self.path_folder_configset / f"{instance.cluster.name}--{instance.app.name}" / "hook")
        Path(local_hook_dir).mkdir(parents=True, exist_ok=True)

        for template_path in sorted(path for path in hook_path.rglob("*") if path.is_file()):
            relative_path = template_path.relative_to(hook_path)
            if relative_path.name.endswith(".mako"):
                relative_path = relative_path.with_name(relative_path.name.removesuffix(".mako"))

            content = self.render_app_template(template_path, instance)
            output_path = local_hook_dir / relative_path
            Path(output_path.parent).mkdir(parents=True, exist_ok=True)
            with open(output_path, "w") as f:
                f.write(content)

            self.add_upload_asset(instance, UploadAsset(
                content=content,
                dest=f"{remote_hook_dir}/{relative_path.as_posix()}",
                owner=self.app_user,
                mode="644",
            ))

        self.shell_script.append(ShellCommand(
            name=f"Run hook {instance.app.name}",
            cmd=f"cd ~/hook/{instance.app.name} && chmod +x hook.sh && ./hook.sh && rm -rf ~/hook/{instance.app.name}",
            for_group=for_group,
            user=self.app_user,
            sudo=True,
            full_login=True,
        ))

    def launch_apps(self) -> None:
        for _, instance in self.instance_apps.items():
            self.shell_script.extend(ShellCollect.up_container(
                instance.app.name,
                "app-net",
                instance.cluster.paths_to_templates["pod"].dest,
                self.app_user,
                instance.cluster.name + "---" + instance.app.name,
            ))

            self.shell_script.extend(ShellCollect.wait_current_app(
                instance.app.name,
                instance.app.role.ports,
                self.app_user,
                instance.cluster.name + "---" + instance.app.name,
            ))

            self.add_app_hook(instance)
