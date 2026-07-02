import base64
from dataclasses import dataclass, field
from shlex import quote

from InfraBaseLib import ShellCommand

@dataclass(kw_only=True)
class Port:
    number: int
    protocol: str
    zone: str

@dataclass(kw_only=True)
class ImageRegistry:
    url: str
    username: str | None = None
    password: str | None = None
    insecure: bool = False

@dataclass(kw_only=True)
class Image:
    path: str
    version: str
    registry: ImageRegistry
    full_name: str = field(init=False)

    def __post_init__(self):
        self.full_name = f"{self.registry.url}/{self.path}:{self.version}"

class ShellCollect:
    @staticmethod
    def _user_systemd_env(user: str) -> str:
        return (f"uid=$(id -u {user}); export XDG_RUNTIME_DIR=/run/user/$uid; "
                "export DBUS_SESSION_BUS_ADDRESS=unix:path=$XDG_RUNTIME_DIR/bus;")

    @staticmethod
    def login_registries(registries: list[ImageRegistry], user: str, for_group: str) -> ShellCommand | None:
        commands = []

        for registry in registries:
            if bool(registry.username) != bool(registry.password):
                raise ValueError("Both image username and password must be set for registry authentication")

            if not registry.username or not registry.password:
                continue

            tls_verify = " --tls-verify=false" if registry.insecure else ""
            username_b64 = base64.b64encode(registry.username.encode("utf-8")).decode("ascii")
            password_b64 = base64.b64encode(registry.password.encode("utf-8")).decode("ascii")
            commands.append(
                f"printf '%s' {quote(password_b64)} | base64 -d "
                f"| podman login {quote(registry.url)} "
                f"--username \"$(printf '%s' {quote(username_b64)} | base64 -d)\" "
                f"--password-stdin{tls_verify}"
            )

        if not commands:
            return None

        return ShellCommand(
            name="Login to Podman registries.yml",
            user=user,
            sudo=True,
            full_login=True,
            for_group=for_group,
            cmd=" && ".join(commands),
        )

    @staticmethod
    def logout_registries(registries: list[ImageRegistry], user: str, for_group: str) -> ShellCommand | None:
        commands = []

        for registry in registries:
            if bool(registry.username) != bool(registry.password):
                raise ValueError("Both image username and password must be set for registry authentication")

            if registry.username and registry.password:
                commands.append(f"podman logout {quote(registry.url)}")

        if not commands:
            return None

        return ShellCommand(
            name="Logout from Podman registries.yml",
            user=user,
            sudo=True,
            full_login=True,
            for_group=for_group,
            cmd=" && ".join(commands),
        )

    @staticmethod
    def download_images(images: list[Image], user: str, for_group: str) -> ShellCommand | None:
        if not images:
            return None

        pull_commands = []

        for image in images:
            image_name = quote(image.full_name)
            tls_verify = " --tls-verify=false" if image.registry.insecure else ""
            pull_commands.append(
                f"podman image exists {image_name} || podman pull{tls_verify} {image_name}"
            )

        cmd = (
            f"printf '%s\\n' {' '.join(quote(command) for command in pull_commands)} "
            f"| xargs -P {len(pull_commands)} -I {{}} sh -c {{}}"
        )

        return ShellCommand(
            name="Download images",
            user=user,
            sudo=True,
            full_login=True,
            for_group=for_group,
            cmd=cmd,
        )

    @staticmethod
    def up_container(name_app: str, network: str, path_to_manifest: str, user: str, role: str) -> list[ShellCommand]:
        return [ShellCommand(
            name=f"Generate Podman unit {name_app}",
            user=user,
            sudo=True,
            full_login=True,
            for_group=role,
            cmd=(
                f"podlet --overwrite --unit-directory --name {name_app} "
                f"podman kube play --network {network} "
                f"\"{path_to_manifest}\""
            ),
        ),
        ShellCommand(
            name=f"Start user container unit {name_app}",
            user="",
            sudo=True,
            full_login=False,
            for_group=role,
            cmd=(
                f"systemctl --user --machine={user}@.host daemon-reload && "
                f"systemctl --user --machine={user}@.host start {name_app}.service"
            ),
        )]

    @staticmethod
    def wait_user_service_active(name_app: str, user: str, role: str, attempts: int = 30,
                                 delay: int = 2) -> ShellCommand:
        return ShellCommand(
            name=f"Wait user service {name_app} active",
            user="",
            sudo=True,
            full_login=False,
            for_group=role,
            cmd=(
                f"for i in $(seq 1 {attempts}); do "
                f"systemctl --user --machine={user}@.host is-active --quiet {name_app}.service && exit 0; "
                f"sleep {delay}; "
                f"done; "
                f"systemctl --user --machine={user}@.host status {name_app}.service --no-pager; "
                f"exit 1"
            ),
        )

    @staticmethod
    def wait_port_listen(port: Port, role: str, attempts: int = 30, delay: int = 2) -> ShellCommand:
        ss_flag = "ltn" if port.protocol == "tcp" else "lun"

        return ShellCommand(
                name=f"Wait {port.number}/{port.protocol} listen",
                user="",
                sudo=True,
                full_login=True,
                for_group=role,
                cmd=f"for i in $(seq 1 {attempts}); do "
                    f"ss -H -{ss_flag} 'sport = :{port.number}' | grep -q . && exit 0; "
                    f"sleep {delay}; "
                    f"done; "
                    f"ss -H -{ss_flag}; "
                    f"exit 1",
            )

    @staticmethod
    def wait_current_app(name_app: str, ports: list[Port], user: str, role: str, attempts: int = 30,
                         delay: int = 2) -> list[ShellCommand]:
        commands = [ShellCollect.wait_user_service_active(name_app, user, role, attempts, delay)]

        for port in ports:
            commands.append(ShellCollect.wait_port_listen(port, role, attempts, delay))

        return commands

    @staticmethod
    def wait_cloud_init(role: str) -> ShellCommand:
        return ShellCommand(
            name="Wait cloud-init complete",
            user="",
            sudo=True,
            full_login=False,
            for_group=role,
            cmd="cloud-init status --wait",
            success_exit_codes=[0,2]
        )


    @staticmethod
    def open_ports(ports: list[Port], role: str) -> list[ShellCommand]:
        if not ports:
            return []

        commands = ["changed=0"]

        for port in ports:
            port_spec = f"{port.number}/{port.protocol}"
            commands.append(
                f"{{ firewall-cmd --permanent "
                f"--zone={port.zone} "
                f"--query-port={port_spec} "
                f"|| {{ firewall-cmd --permanent "
                f"--zone={port.zone} "
                f"--add-port={port_spec}; changed=1; }}"
                f"; }}"
            )

        commands.append('{ [ "$changed" -eq 0 ] || firewall-cmd --reload; }')
        ports_label = ", ".join(f"{port.number}/{port.protocol}:{port.zone}" for port in ports)

        return [
            ShellCommand(
                name=f"Open firewalld ports {ports_label}", user="", sudo=True, full_login=False,
                for_group=role,
                cmd=" && ".join(commands),
            ),
        ]

    @staticmethod
    def setting_podman_app_runtime(user: str, role: str) -> list[ShellCommand]:
        app_home = f"/home/{user}"
        return [
            ShellCommand(
                name="Ensure firewalld is running", user="", sudo=True, full_login=False,
                for_group=role,
                cmd="systemctl is-enabled --quiet firewalld || systemctl enable firewalld; "
                    "systemctl is-active --quiet firewalld || systemctl start firewalld",
            ),
            ShellCommand(
                name=f"Enable linger for {user}", user="", sudo=True, full_login=False,
                for_group=role,
                cmd=f"loginctl show-user {user} --property=Linger "
                    f"| grep -q '^Linger=yes$' "
                    f"|| loginctl enable-linger {user}",
                        ),
            ShellCommand(
                name=f"Start user systemd manager for {user}", user="", sudo=True, full_login=False,
                for_group=role,
                cmd=f"systemctl is-active --quiet user@$(id -u {user}).service "
                    f"|| systemctl start user@$(id -u {user}).service",
            ),
            ShellCommand(
                name=f"Start user DBus socket for {user}",
                user="",
                sudo=True,
                full_login=False,
                for_group=role,
                cmd=f"systemctl --user --machine={user}@.host start dbus.socket",
            ),
            ShellCommand(
                name=f"Create Podman config dirs for {user}", user="", sudo=True, full_login=False,
                for_group=role,
                cmd=f"install -d "
                    f"-o {user} "
                    f"-g $(id -gn {user}) "
                    f"-m 700 "
                    f"{app_home}/.config "
                    f"{app_home}/.config/containers "
                    f"{app_home}/.config/containers/systemd",
            ),
            ShellCommand(
                name="Create Podman network app-net", user=user, sudo=True, full_login=True,
                for_group=role,
                cmd="podman network exists app-net || podman network create app-net",
            ),

            ]
