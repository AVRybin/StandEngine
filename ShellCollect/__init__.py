from InfraBaseLib import ShellCommand

class ShellCollect:
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