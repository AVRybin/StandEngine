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
