"""Test doubles for the Nova Execution Kernel.

Phase C7 — a fake kernel for unit tests.  Tests register a handler that
receives each :class:`ExecutionRequest` and returns either a
``(exit_code, stdout, stderr)`` tuple or a full :class:`ExecutionResult`.
Every request is recorded on ``.requests`` for assertions.

Usage::

    from deerflow.execution.testing import FakeExecutionKernel
    from deerflow.services.container import service_container

    fake = FakeExecutionKernel(lambda req: (0, "ok", ""))
    service_container.override(execution_kernel=fake)
    ...
    assert fake.requests[0].argv[0] == "docker"
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any

from deerflow.execution.audit import AuditEngine
from deerflow.execution.metrics import ExecutionMetrics
from deerflow.execution.models import (
    ExecutionRequest,
    ExecutionResult,
    ExecutionStatus,
    now_iso,
)

Handler = Callable[[ExecutionRequest], "tuple[int, str, str] | ExecutionResult"]


class FakeExecutionKernel:
    """In-memory kernel satisfying ExecutionKernelProtocol for tests.

    Phase C8: includes a FakeSupervisor with the minimal interface RunManager
    needs for two-phase cancellation tests.
    """

    def __init__(self, handler: Handler | None = None) -> None:
        self.handler: Handler = handler or (lambda _req: (0, "", ""))
        self.requests: list[ExecutionRequest] = []
        self.audit_engine = AuditEngine()
        self.metrics = ExecutionMetrics()
        self.supervisor = FakeSupervisor()

    def execute_sync(self, request: ExecutionRequest) -> ExecutionResult:
        self.requests.append(request)
        outcome = self.handler(request)
        if isinstance(outcome, ExecutionResult):
            result = outcome
        else:
            exit_code, stdout, stderr = outcome
            result = ExecutionResult(
                execution_id=request.execution_id,
                status=ExecutionStatus.SUCCEEDED if exit_code == 0 else ExecutionStatus.FAILED,
                exit_code=exit_code,
                stdout=stdout,
                stderr=stderr,
                started_at=now_iso(),
                finished_at=now_iso(),
                error=None if exit_code == 0 else f"exit code {exit_code}",
                execution_class=request.execution_class,
                correlation_id=request.correlation_id,
            )
        self.audit_engine.record(request, result)
        self.metrics.observe(request.execution_class, result.status, result.duration_ms)
        return result

    async def execute(self, request: ExecutionRequest) -> ExecutionResult:
        return self.execute_sync(request)

    async def spawn(self, request: ExecutionRequest, **_kwargs: Any):  # noqa: ANN201
        raise NotImplementedError("FakeExecutionKernel does not spawn processes")

    def cancel(self, execution_id: str, grace_period: float = 5.0) -> bool:
        return False

    def shutdown(self) -> int:
        return 0

    def snapshot(self) -> dict[str, Any]:
        return {
            "metrics": self.metrics.snapshot(),
            "audit_size": self.audit_engine.size,
        }


def timeout_result(request: ExecutionRequest, timeout: float = 5.0) -> ExecutionResult:
    """Build a TIMED_OUT result for *request* — convenience for handlers."""
    return ExecutionResult(
        execution_id=request.execution_id,
        status=ExecutionStatus.TIMED_OUT,
        exit_code=None,
        started_at=now_iso(),
        finished_at=now_iso(),
        error=f"timed out after {timeout}s",
        execution_class=request.execution_class,
        correlation_id=request.correlation_id,
    )


class FakeSupervisor:
    """Minimal supervisor mock for tests.

    Phase C8: provides the get_execution_id() and cancel() interface
    that RunManager.cancel() needs for two-phase cancellation.
    """

    def __init__(self) -> None:
        self._run_to_execution: dict[str, str] = {}
        self._cancelled: list[str] = []

    def register_execution(
        self,
        execution_id: str,
        *,
        run_id: str = "",
        session_id: str = "",
        pid: int = 0,
        parent_execution_id: str = "",
    ) -> None:
        if run_id:
            self._run_to_execution[run_id] = execution_id

    def unregister_execution(self, execution_id: str) -> None:
        for k, v in list(self._run_to_execution.items()):
            if v == execution_id:
                del self._run_to_execution[k]

    def get_execution_id(self, run_id: str) -> str | None:
        return self._run_to_execution.get(run_id)

    def cancel(self, execution_id: str, grace_period: float = 5.0, *, recursive: bool = True) -> bool:
        self._cancelled.append(execution_id)
        return True

    def register_popen(
        self,
        execution_id: str,
        proc: Any,
        *,
        run_id: str = "",
        pid: int = 0,
        parent_execution_id: str = "",
    ) -> None:
        if run_id:
            self._run_to_execution[run_id] = execution_id

    def unregister_popen(self, execution_id: str) -> None:
        pass

    def register_spawned(
        self,
        handle: Any,
        *,
        run_id: str = "",
        session_id: str = "",
        parent_execution_id: str = "",
    ) -> None:
        if run_id:
            self._run_to_execution[run_id] = handle.execution_id

    def unregister_spawned(self, execution_id: str) -> None:
        pass

    def update_heartbeat(self, execution_id: str, **kwargs: Any) -> None:
        pass

    def get_heartbeat(self, execution_id: str) -> None:
        return None

    def scan_zombies(self, heartbeat_timeout: float = 30.0) -> list[str]:
        return []

    def snapshot(self) -> dict[str, Any]:
        return {
            "popen_tracked": 0,
            "spawned_tracked": 0,
            "execution_to_run": {},
            "run_to_execution": dict(self._run_to_execution),
            "cancelled": list(self._cancelled),
        }
