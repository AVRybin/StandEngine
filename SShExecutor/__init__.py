from dataclasses import dataclass, field
from typing import Any, Callable

from paramiko import PKey
from pyinfra.api import Config as infraConfig, Inventory, State as infraState
from pyinfra.api.connect import connect_all, disconnect_all
from pyinfra.api.operation import add_op
from pyinfra.api.operations import run_ops
from pyinfra.operations import server as op_server


@dataclass(kw_only=True)
class SShExecutor:
    user: str
    key: PKey
    servers: list[str]
    state: infraState = field(init=False)

    @staticmethod
    def get_shell_command(*, name: str, cmd: str, user: str, sudo: bool, full_login: bool) \
            -> tuple[Callable[..., Any], dict[str, Any]]:
        kwargs = {
            "name": name,
            "commands": [
                cmd,
            ],
            "_sudo": sudo,
            "_use_sudo_login": full_login,
        }

        if user:
            kwargs["_sudo_user"] = user

        return op_server.shell, kwargs

    def __post_init__(self):
        inventory = Inventory(
            (
                [
                    (
                        server,
                        {
                            "ssh_user": self.user,
                            "ssh_paramiko_connect_kwargs": {"pkey": self.key},
                            "ssh_allow_agent": False,
                            "ssh_look_for_keys": False,
                            "ssh_strict_host_key_checking": "no",
                            "ssh_known_hosts_file": "/dev/null",
                        },
                    )
                    for server in self.servers
                ],
                {},
            ),
        )

        pyinfra_config = infraConfig(
            SUDO=False,
        )

        self.state = infraState(
            inventory=inventory,
            config=pyinfra_config,
        )

    def run(self, operations: list[tuple[Callable[..., Any], dict[str, Any]]]) -> None:
        connect_all(self.state)

        try:
            for operation, kwargs in operations:
                add_op(
                    self.state,
                    operation,
                    **kwargs,
                )

            run_ops(self.state)
        finally:
            disconnect_all(self.state)
