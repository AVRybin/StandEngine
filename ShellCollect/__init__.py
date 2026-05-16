from dataclasses import dataclass
from InfraBaseLib import ShellCommand

@dataclass(kw_only=True)
class Port:
    number: int
    protocol: str
    zone: str

@dataclass(kw_only=True)
class Image:
    path: str
    version: str
    registry: str

class ShellCollect:
    @staticmethod
    def _user_systemd_env(user: str) -> str:
        return (f"uid=$(id -u {user}); export XDG_RUNTIME_DIR=/run/user/$uid; "
                "export DBUS_SESSION_BUS_ADDRESS=unix:path=$XDG_RUNTIME_DIR/bus;")

    @staticmethod
    def download_image(image: Image, user: str, role: str) -> ShellCommand:
        image_name = f"{image.registry}/{image.path}:{image.version}"
        return ShellCommand(
                name=f"Download Podman image {image_name}",
                user=user,
                sudo=True,
                full_login=True,
                for_group=role,
                cmd=f"podman image exists {image_name} || podman pull {image_name}",
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
    def open_ports(ports: list[Port], role: str) -> list[ShellCommand]:
        if not ports:
            return []

        commands = [
            ShellCommand(
                name="Ensure firewalld is running", user="", sudo=True, full_login=False,
                for_group=role,
                cmd="systemctl enable --now firewalld",
            ),
        ]

        for port in ports:
            port_spec = f"{port.number}/{port.protocol}"
            commands.append(
                ShellCommand(
                    name=f"Open {port_spec} in firewalld zone {port.zone}",
                    user="",
                    sudo=True,
                    full_login=False,
                    for_group=role,
                    cmd=f"firewall-cmd --permanent "
                        f"--zone={port.zone} "
                        f"--query-port={port_spec} "
                        f"|| firewall-cmd --permanent "
                        f"--zone={port.zone} "
                        f"--add-port={port_spec}",
                ),
            )

        commands.append(
            ShellCommand(
                name="Reload firewalld", user="", sudo=True, full_login=False,
                for_group=role,
                cmd="firewall-cmd --reload",
            ),
        )

        return commands

    @staticmethod
    def setting_podman_app_runtime(user: str, role: str) -> list[ShellCommand]:
        app_home = f"/home/{user}"
        return [
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
