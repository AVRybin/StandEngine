from dataclasses import dataclass
from datetime import datetime
from time import perf_counter
from typing import Any

from pyinfra.api import State as infraState
from pyinfra.api.state import BaseStateCallback


@dataclass(frozen=True, kw_only=True)
class SShExecutorDiagnostArgs:
    operation_events: bool = True
    host_summary: bool = True


class PyinfraDiagnostic(BaseStateCallback):
    start_time: dict[tuple[str, str], float]
    host_stats: dict[str, "PyinfraHostStats"]

    def __init__(self, operation_events: bool = True, host_summary: bool = True):
        self.start_time = {}
        self.host_stats = {}
        self.operation_events = operation_events
        self.host_summary = host_summary

    def host_disconnect(self, state: infraState, host: Any):
        if not self.host_summary:
            return

        stats = self.host_stats.get(host.name)
        if stats is not None and not stats.summary_printed:
            self.print_host_summary(host.name, stats, status="incomplete")

    def operation_host_start(self, state: infraState, host: Any, op_hash: str):
        if op_hash not in state.ops[host]:
            return

        self.record_host_start(state, host)
        if self.operation_events:
            self.start_time[(host.name, op_hash)] = perf_counter()
            self.print_event("start", state, host, op_hash)

    def operation_host_success(self, state: infraState, host: Any, op_hash: str, retry_count: int = 0):
        if self.operation_events:
            self.print_event("success", state, host, op_hash, retry_count=retry_count)
        self.record_host_finish(host)

    def operation_host_error(
        self,
        state: infraState,
        host: Any,
        op_hash: str,
        retry_count: int = 0,
        max_retries: int = 0,
    ):
        if self.operation_events:
            self.print_event(
                "error",
                state,
                host,
                op_hash,
                retry_count=retry_count,
                max_retries=max_retries,
            )
        self.record_host_finish(host, error=True)

    def operation_host_retry(self, state: infraState, host: Any, op_hash: str, retry_num: int, max_retries: int):
        self.record_host_retry(host)
        if self.operation_events:
            self.print_event(
                "retry",
                state,
                host,
                op_hash,
                retry_count=retry_num,
                max_retries=max_retries,
            )

    def record_host_start(self, state: infraState, host: Any) -> None:
        if not self.host_summary:
            return

        stats = self.host_stats.setdefault(
            host.name,
            PyinfraHostStats(expected_operations=len(state.ops[host])),
        )
        stats.started += 1
        if stats.started_at is None:
            stats.started_at = perf_counter()

    def record_host_finish(self, host: Any, error: bool = False) -> None:
        if not self.host_summary:
            return

        stats = self.host_stats.get(host.name)
        if stats is None:
            return

        stats.completed += 1
        if error:
            stats.errors += 1
        else:
            stats.success += 1

        if stats.completed >= stats.expected_operations and not stats.summary_printed:
            self.print_host_summary(host.name, stats, status="complete")

    def record_host_retry(self, host: Any) -> None:
        if not self.host_summary:
            return

        stats = self.host_stats.get(host.name)
        if stats is not None:
            stats.retries += 1

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
            "[pyinfra-diagnostic]",
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

    def print_host_summary(self, host_name: str, stats: "PyinfraHostStats", status: str) -> None:
        now = datetime.now().isoformat(timespec="milliseconds")
        duration_ms = 0
        if stats.started_at is not None:
            duration_ms = int((perf_counter() - stats.started_at) * 1000)

        parts = [
            "[pyinfra-host-summary]",
            f"time={now}",
            f"status={status}",
            f"host={host_name}",
            f"operations={stats.started}",
            f"completed={stats.completed}",
            f"success={stats.success}",
            f"errors={stats.errors}",
            f"retries={stats.retries}",
            f"duration_ms={duration_ms}",
        ]

        print(" ".join(parts))
        stats.summary_printed = True


@dataclass
class PyinfraHostStats:
    expected_operations: int
    started: int = 0
    completed: int = 0
    success: int = 0
    errors: int = 0
    retries: int = 0
    started_at: float | None = None
    summary_printed: bool = False
