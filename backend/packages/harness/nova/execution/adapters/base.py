"""Shared base for execution adapters.

Phase C7 — every adapter holds a kernel reference and stamps tracing
context onto the requests it builds.
"""

from __future__ import annotations

from deerflow.execution.models import (
    ExecutionClass,
    ExecutionRequest,
    ResourceLimits,
)
from deerflow.execution.protocols import ExecutionKernelProtocol


class BaseAdapter:
    """Common request-building plumbing for adapters."""

    execution_class: ExecutionClass = ExecutionClass.SHELL

    def __init__(self, kernel: ExecutionKernelProtocol) -> None:
        self._kernel = kernel

    @property
    def kernel(self) -> ExecutionKernelProtocol:
        return self._kernel

    def _request(
        self,
        argv: tuple[str, ...] | list[str],
        *,
        cwd: str | None = None,
        env: dict[str, str] | None = None,
        stdin: str | None = None,
        timeout: float = 60.0,
        queue_timeout: float = 30.0,
        grace_period: float = 5.0,
        intent: str = "",
        correlation_id: str = "",
        run_id: str = "",
        thread_id: str = "",
    ) -> ExecutionRequest:
        return ExecutionRequest(
            argv=tuple(argv),
            execution_class=self.execution_class,
            cwd=cwd,
            env=env,
            stdin=stdin,
            limits=ResourceLimits(
                timeout=timeout,
                queue_timeout=queue_timeout,
                grace_period=grace_period,
            ),
            intent=intent,
            correlation_id=correlation_id,
            run_id=run_id,
            thread_id=thread_id,
        )
