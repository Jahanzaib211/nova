"""Typed return models and canonical state for the Nova service layer.

Phase C2 — adds ``RunState`` as the immutable canonical runtime object.
Existing ``RunRecord`` (mutable, in-memory) remains for backward compat;
``RunState`` is the read-only view passed across service boundaries.

Usage::

    from deerflow.services.types import RunState, RunDetail

    state = RunState(run_id="abc", thread_id="t1", status="running")
    detail = RunDetail(run_id="abc", thread_id="t1", assistant_id="lead-agent", status="pending")
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

# ---------------------------------------------------------------------------
# Canonical run state (Phase C2)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class RunState:
    """Immutable canonical representation of a run's current state.

    This is the read-only view that flows across service boundaries.
    Mutable run lifecycle is still managed by ``RunManager`` internally;
    ``RunState`` is the projection returned to callers.
    """

    run_id: str
    thread_id: str
    status: str
    correlation_id: str = ""
    assistant_id: str = "lead-agent"
    model_name: str | None = None
    created_at: str | None = None
    updated_at: str | None = None
    error: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)
    total_tokens: int = 0
    message_count: int = 0
    store_only: bool = False

    @classmethod
    def from_record(cls, record: Any) -> RunState:
        """Create a RunState from a RunRecord (backward-compat bridge)."""
        status = record.status
        if hasattr(status, "value"):
            status = status.value
        return cls(
            run_id=record.run_id,
            thread_id=record.thread_id,
            status=str(status),
            correlation_id=getattr(record, "correlation_id", ""),
            assistant_id=getattr(record, "assistant_id", "lead-agent"),
            model_name=getattr(record, "model_name", None),
            created_at=getattr(record, "created_at", None),
            error=getattr(record, "error", None),
            total_tokens=getattr(record, "total_tokens", 0),
            message_count=getattr(record, "message_count", 0),
            store_only=getattr(record, "store_only", False),
        )


# ---------------------------------------------------------------------------
# Service return models (Phase C1)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class RunSummary:
    """Lightweight run representation for list operations."""

    run_id: str
    thread_id: str
    status: str
    correlation_id: str = ""
    model_name: str | None = None
    created_at: str | None = None


@dataclass(frozen=True)
class RunDetail:
    """Full run representation for detail views."""

    run_id: str
    thread_id: str
    assistant_id: str
    status: str
    correlation_id: str = ""
    model_name: str | None = None
    created_at: str | None = None
    error: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)
    total_tokens: int = 0
    message_count: int = 0


@dataclass(frozen=True)
class ProbeResult:
    """Result of a single health probe."""

    name: str
    healthy: bool
    message: str = ""
    latency_ms: float = 0.0


@dataclass(frozen=True)
class HealthReport:
    """Aggregated health report from all probes."""

    healthy: bool
    probes: list[ProbeResult] = field(default_factory=list)
    probe_count: int = 0
    healthy_count: int = 0


@dataclass(frozen=True)
class RecoveryAction:
    """Record of an auto-recovery action taken."""

    component: str
    action: str
    success: bool
    message: str = ""


@dataclass(frozen=True)
class DiagnosticsRecord:
    """A single diagnostics trace record."""

    seq: int
    stage: str
    run_id: str = ""
    thread_id: str = ""
    correlation_id: str = ""
    wall_iso: str = ""
    extra: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class WorkspacePaths:
    """Resolved workspace paths for a thread."""

    workspace: str
    uploads: str
    outputs: str
    user_data_base: str = ""
