from dataclasses import dataclass, field
from pathlib import Path

from ShellCollect import ShellCommand, Port, Image, ShellCollect

@dataclass(kw_only=True)
class App:
    name: str
    role: RoleApp
    preferences: dict[str, str] = field(default_factory=dict)

@dataclass(kw_only=True)
class RoleApp:
    name: str
    ports: list[Port]
    preferences: dict[str, str] = field(default_factory=dict)

@dataclass(kw_only=True)
class ConfigFile:
    paths_to_templates: Path
    dest: str
    owner: str
    mode: str

@dataclass(kw_only=True)
class ClusterApp:
    name: str
    image: Image
    preferences: dict[str, str] = field(default_factory=dict)
    instances_app: list[App] = field(default_factory=list)
    paths_to_templates: dict[str, ConfigFile] = field(default_factory=list)

    def get_shell_install(self, user: str) -> list[ShellCommand]:
        shell = []
        roles = {}

        for instance in self.instances_app:
            if instance.role.name not in roles:
                roles[instance.role.name] = 1
                shell.extend(ShellCollect.open_ports(instance.role.ports, self.name + "___" + instance.role.name))

        shell.append(ShellCollect.download_image(self.image, user, self.name))
        return shell