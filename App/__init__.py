from dataclasses import dataclass
from ShellCollect import ShellCommand, Port, Image, ShellCollect

@dataclass(kw_only=True)
class RoleApp:
    ports: list[Port]

@dataclass(kw_only=True)
class App:
    name: str
    roles: dict[str, RoleApp]
    image: Image

    def get_shell_install(self, user: str) -> list[ShellCommand]:
        shell = []

        for role in self.roles:
            shell = ShellCollect.open_ports(self.roles[role].ports, self.name + "___" + role)

        shell.append(ShellCollect.download_image(self.image, user, self.name))
        return shell