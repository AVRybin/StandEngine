from dataclasses import dataclass, field
from typing import Any, Callable

from paramiko import PKey
from pyinfra.api import Config as infraConfig, Inventory, State as infraState
from pyinfra.api.connect import connect_all, disconnect_all
from pyinfra.api.operation import add_op
from pyinfra.api.operations import run_ops
from pyinfra.operations import server as op_server


@dataclass(kw_only=True)
class ShellCommand:
    name: str
    cmd: str
    for_group: str
    user: str = ""
    sudo: bool = False
    full_login: bool = False

    operation: Callable[..., Any] = field(init=False)

    def __post_init__(self):
        self.operation = op_server.shell

    def build_kwargs(self, inventory: Inventory) -> dict[str, Any]:
        kwargs: dict[str, Any] = {
            "name": self.name,
            "commands": [
                self.cmd,
            ],
            "_sudo": self.sudo,
            "_use_sudo_login": self.full_login,
            "host": inventory.get_group(self.for_group),
        }

        if self.user:
            kwargs["_sudo_user"] = self.user

        return kwargs


@dataclass(kw_only=True)
class SShExecutor:
    user: str
    key: PKey
    server: dict[str, list[str]]
    state: infraState = field(init=False)

    def __post_init__(self):
        host_data = {
            "ssh_user": self.user,
            "ssh_paramiko_connect_kwargs": {"pkey": self.key},
            "ssh_allow_agent": False,
            "ssh_look_for_keys": False,
            "ssh_strict_host_key_checking": "no",
            "ssh_known_hosts_file": "/dev/null",
        }

        group_hosts: dict[str, list[str]] = {}
        for ip_address, groups in self.server.items():
            for group in groups:
                group_hosts.setdefault(group, []).append(ip_address)

        inventory = Inventory(
            (
                [
                    (
                        ip_address,
                        host_data,
                    )
                    for ip_address in self.server
                ],
                {},
            ),
            **{
                group: (ip_addresses, {})
                for group, ip_addresses in group_hosts.items()
            },
        )

        pyinfra_config = infraConfig(
            SUDO=False,
        )

        self.state = infraState(
            inventory=inventory,
            config=pyinfra_config,
        )

    def run(self, operations: list[ShellCommand]) -> None:
        connect_all(self.state)

        try:
            for operation in operations:
                add_op(
                    self.state,
                    operation.operation,
                    **operation.build_kwargs(self.state.inventory),
                )

            run_ops(self.state)
        finally:
            disconnect_all(self.state)
