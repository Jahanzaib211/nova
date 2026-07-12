"""Typed return models for the Nova service layer.

These are pure data containers with no behavior. They define the shapes
that service methods return, enabling callers to depend on stable types
rather than raw dicts.

Phase C1 — architecture preparation only. No runtime behavior changes.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


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
