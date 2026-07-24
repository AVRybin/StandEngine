from dataclasses import dataclass, field
from pathlib import Path

from ShellCollect import ShellCommand, Port, Image, ShellCollect

@dataclass(kw_only=True)
class App:
    name: str
    role: RoleApp
    cpu: int
    ram: int
    oom_priority: int | None = None
    hook_path: Path | None = None
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
    connection_template: Path | None = None
    connection_instance_name: str | None = None

    @property
    def instance_count(self) -> int:
        return len(self.instances_app)
