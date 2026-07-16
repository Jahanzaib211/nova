"""Replay engine for the Nova Execution Kernel.

Phase C7 — deterministic re-execution of audited executions.  Given an
execution id from the audit trail, the replay engine reconstructs the
original request and either describes it (``dry_run``) or re-executes it
through the kernel (``replay``) with full policy/audit/metrics treatment.

Environment variable values are never stored in the audit trail, so a
replayed execution inherits the current process environment.  Replays are
labelled ``replay_of=<original id>`` in the audit trail so they are
distinguishable from first-run executions.
"""

from __future__ import annotations

import logging
from typing import Any

from deerflow.execution.audit import AuditEngine
from deerflow.execution.models import (
    ExecutionClass,
    ExecutionRequest,
    ExecutionResult,
    ResourceLimits,
)

logger = logging.getLogger(__name__)


class ReplayEngine:
    """Rebuilds and re-executes audited executions."""

    def __init__(self, kernel: Any, audit_engine: AuditEngine) -> None:
        # ``kernel`` is typed Any to avoid a circular import with kernel.py;
        # it satisfies ExecutionKernelProtocol.
        self._kernel = kernel
        self._audit = audit_engine

    def reconstruct(self, execution_id: str) -> ExecutionRequest | None:
        """Rebuild the request for an audited execution, or None."""
        record = self._audit.find(execution_id)
        if record is None:
            return None
        return ExecutionRequest(
            argv=tuple(record.argv),
            execution_class=ExecutionClass(record.execution_class),
            cwd=record.cwd,
            intent=record.intent or f"replay of {execution_id}",
            correlation_id=record.correlation_id,
            run_id=record.run_id,
            thread_id=record.thread_id,
            limits=ResourceLimits(),
            labels={"replay_of": execution_id},
        )

    def dry_run(self, execution_id: str) -> dict[str, Any] | None:
        """Describe what a replay would execute, without executing it."""
        request = self.reconstruct(execution_id)
        if request is None:
            return None
        return {
            "execution_id": execution_id,
            "argv": list(request.argv),
            "execution_class": request.execution_class.value,
            "cwd": request.cwd,
            "intent": request.intent,
        }

    def replay(self, execution_id: str) -> ExecutionResult | None:
        """Re-execute an audited execution through the kernel."""
        request = self.reconstruct(execution_id)
        if request is None:
            logger.warning("Replay requested for unknown execution %s", execution_id)
            return None
        logger.info("Replaying execution %s as %s", execution_id, request.execution_id)
        return self._kernel.execute_sync(request)
