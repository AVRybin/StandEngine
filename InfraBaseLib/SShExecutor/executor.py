from dataclasses import dataclass, field
from typing import Any, Callable, Protocol

from paramiko import PKey
from pyinfra.api import Config as infraConfig, Inventory, State as infraState
from pyinfra.api.connect import connect_all, disconnect_all
from pyinfra.api.operation import add_op
from pyinfra.api.operations import run_ops
from pyinfra.operations import files, server as op_server

from InfraBaseLib.SShExecutor.diagnostic import PyinfraDiagnostic, SShExecutorDiagnostArgs
from InfraBaseLib.SShExecutor.uploder import UploadAsset, UploadFilesCollector


class InfraOperation(Protocol):
    operation: Callable[..., Any]

    def build_kwargs(self, inventory: Inventory) -> dict[str, Any]: ...


@dataclass(kw_only=True)
class ShellCommand:
    name: str
    cmd: str
    for_group: str
    user: str = ""
    sudo: bool = False
    full_login: bool = False
    success_exit_codes: list[int] = field(default_factory=lambda: [0])

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
            "_success_exit_codes": self.success_exit_codes,
            "host": inventory.get_group(self.for_group),
        }

        if self.user:
            kwargs["_sudo_user"] = self.user

        return kwargs


@dataclass(kw_only=True)
class EnsureDirectory:
    name: str
    path: str
    for_group: str
    user: str = ""
    mode: str = "755"
    sudo: bool = True
    operation: Callable[..., Any] = field(init=False)

    def __post_init__(self):
        self.operation = files.directory

    def build_kwargs(self, inventory: Inventory) -> dict[str, Any]:
        kwargs: dict[str, Any] = {
            "name": self.name,
            "path": self.path,
            "present": True,
            "mode": self.mode,
            "host": inventory.get_group(self.for_group),
            "_sudo": self.sudo,
        }
        if self.user:
            kwargs["user"] = self.user
        return kwargs


@dataclass(kw_only=True)
class SShExecutor:
    user: str
    key: PKey
    server: dict[str, list[str]]
    state: infraState = field(init=False)
    uploader: UploadFilesCollector = field(default_factory=UploadFilesCollector, init=False)

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

    def add_upload_asset(self, node_group: str, asset: UploadAsset) -> None:
        self.uploader.add_upload_asset(node_group, asset)

    def clear_upload_files(self) -> None:
        self.uploader.clear_upload_files()

    def run(
        self,
        operations: list[InfraOperation],
        diagnostic: bool | SShExecutorDiagnostArgs = False,
        app_user: str = "",
        preflight_operations: list[InfraOperation] | None = None,
    ) -> None:
        if isinstance(diagnostic, SShExecutorDiagnostArgs):
            self.state.add_callback_handler(
                PyinfraDiagnostic(
                    operation_events=diagnostic.operation_events,
                    host_summary=diagnostic.host_summary,
                )
            )
        elif diagnostic:
            self.state.add_callback_handler(PyinfraDiagnostic())

        if self.uploader.upload_files:
            if not app_user:
                raise ValueError("app_user is required to upload files archive")
            operations = self.uploader.build_upload_archive_operations(app_user) + operations

        if preflight_operations:
            operations = preflight_operations + operations

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
