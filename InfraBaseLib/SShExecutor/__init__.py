from dataclasses import dataclass, field
from typing import Any, Callable, Protocol
from io import StringIO
from datetime import datetime
from time import perf_counter

from paramiko import PKey
from pyinfra.api import Config as infraConfig, Inventory, State as infraState
from pyinfra.api.connect import connect_all, disconnect_all
from pyinfra.api.operation import add_op
from pyinfra.api.operations import run_ops
from pyinfra.api.state import BaseStateCallback
from pyinfra.operations import server as op_server, files

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
class UploadFile:
    name: str
    content: str
    dest: str
    for_group: str
    user: str
    mode: str
    operation: Callable[..., Any] = field(init=False)

    def __post_init__(self):
        self.operation = files.put

    def build_kwargs(self, inventory: Inventory) -> dict[str, Any]:
        kwargs: dict[str, Any] = {
            "name": self.name,
            "src": StringIO(self.content),
            "dest": self.dest,
            "mode": self.mode,
            "host": inventory.get_group(self.for_group),
            "_sudo": True,
        }
        if self.user:
            kwargs["user"] = self.user
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


class PyinfraDiagnostic(BaseStateCallback):
    start_time: dict[tuple[str, str], float]

    def __init__(self):
        self.start_time = {}

    def operation_host_start(self, state: infraState, host: Any, op_hash: str):
        if op_hash not in state.ops[host]:
            return

        self.start_time[(host.name, op_hash)] = perf_counter()
        self.print_event("start", state, host, op_hash)

    def operation_host_success(self, state: infraState, host: Any, op_hash: str, retry_count: int = 0):
        self.print_event("success", state, host, op_hash, retry_count=retry_count)

    def operation_host_error(
        self,
        state: infraState,
        host: Any,
        op_hash: str,
        retry_count: int = 0,
        max_retries: int = 0,
    ):
        self.print_event(
            "error",
            state,
            host,
            op_hash,
            retry_count=retry_count,
            max_retries=max_retries,
        )

    def operation_host_retry(self, state: infraState, host: Any, op_hash: str, retry_num: int, max_retries: int):
        self.print_event(
            "retry",
            state,
            host,
            op_hash,
            retry_count=retry_num,
            max_retries=max_retries,
        )

    def print_event(
        self,
        status: str,
        state: infraState,
        host: Any,
        op_hash: str,
        retry_count: int = 0,
        max_retries: int = 0,
    ):
        op_meta = state.get_op_meta(op_hash)
        op_name = ", ".join(sorted(op_meta.names)) or "Operation"
        now = datetime.now().isoformat(timespec="milliseconds")

        parts = [
            f"[pyinfra-diagnostic]",
            f"time={now}",
            f"status={status}",
            f"host={host.name}",
            f"order={'.'.join(str(item) for item in op_meta.op_order)}",
            f"operation={op_name!r}",
        ]

        start_time = self.start_time.get((host.name, op_hash))
        if start_time is not None and status in ["success", "error"]:
            parts.append(f"duration_ms={int((perf_counter() - start_time) * 1000)}")
            del self.start_time[(host.name, op_hash)]

        if retry_count:
            parts.append(f"retry={retry_count}")

        if max_retries:
            parts.append(f"max_retries={max_retries}")

        print(" ".join(parts))


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

    def run(self, operations: list[InfraOperation], diagnostic: bool = False) -> None:
        if diagnostic:
            self.state.add_callback_handler(PyinfraDiagnostic())

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
