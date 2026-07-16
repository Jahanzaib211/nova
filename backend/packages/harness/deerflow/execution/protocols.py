"""Protocol interfaces for the Nova Execution Kernel.

Phase C7 — structural typing for the kernel surface, following the same
``typing.Protocol`` pattern as ``deerflow.services.protocols`` (Phase C1).
Callers depend on these protocols, never on concrete classes.
"""

from __future__ import annotations

from typing import Any, Protocol, runtime_checkable

from deerflow.execution.models import (
    ExecutionRequest,
    ExecutionResult,
)
from deerflow.execution.supervisor import SpawnedProcess


@runtime_checkable
class ExecutionKernelProtocol(Protocol):
    """The single execution surface of the platform.

    Lifecycle expectations:
        - ``execute``/``execute_sync`` run a request to completion and
          always return a typed result (never raise for process failures).
        - ``spawn`` starts a supervised long-running process.
        - ``cancel`` interrupts a running execution by id.
        - ``shutdown`` reconciles every tracked process.

    Telemetry expectations:
        - Every execution is audited, measured, and emits domain events.
        - Correlation IDs propagate through all operations.
    """

    def execute_sync(self, request: ExecutionRequest) -> ExecutionResult:
        """Run an execution to completion (blocking, thread-safe)."""
        ...

    async def execute(self, request: ExecutionRequest) -> ExecutionResult:
        """Run an execution to completion without blocking the event loop."""
        ...

    async def spawn(
        self,
        request: ExecutionRequest,
        *,
        start_new_session: bool = True,
        merge_stderr: bool = True,
    ) -> SpawnedProcess:
        """Start a supervised long-running process."""
        ...

    def cancel(self, execution_id: str, grace_period: float = 5.0) -> bool:
        """Cancel a running execution by id."""
        ...

    def shutdown(self) -> int:
        """Terminate all tracked processes; returns count reconciled."""
        ...

    def snapshot(self) -> dict[str, Any]:
        """Introspection: metrics, resources, supervisor, audit state."""
        ...


@runtime_checkable
class ExecutionAdapterProtocol(Protocol):
    """An adapter translates domain intent into kernel requests.

    Adapters never create processes; they build ``ExecutionRequest``
    objects and hand them to the kernel.  Each adapter owns exactly one
    ``ExecutionClass``.
    """

    @property
    def kernel(self) -> ExecutionKernelProtocol:
        """The kernel this adapter dispatches through."""
        ...
