"""Domain events for the Workspace Intelligence Kernel.

Phase C9 — immutable frozen dataclasses representing WIK domain events.
All events carry workspace root and timestamp for traceability.

Usage::

    from deerflow.workspace.events import WorkspaceScanned, PlanBuilt

    event = WorkspaceScanned(
        root_path="/home/jahanzaib/Desktop/nova/backend",
        file_count=761,
        project_count=3,
        symbol_count=17394,
        scan_duration_ms=850.0,
    )
    assert isinstance(event, WorkspaceEvent)
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any


def _now_iso() -> str:
    return datetime.now(UTC).isoformat()


# ---------------------------------------------------------------------------
# Base event
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class WorkspaceEvent:
    """Base class for all WIK events."""

    root_path: str = ""
    occurred_at: str = field(default_factory=_now_iso)


# ---------------------------------------------------------------------------
# Scan events
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class WorkspaceScanStarted(WorkspaceEvent):
    """Fired when a workspace scan begins."""

    scan_id: str = ""


@dataclass(frozen=True)
class WorkspaceScanned(WorkspaceEvent):
    """Fired when a workspace scan completes successfully."""

    scan_id: str = ""
    file_count: int = 0
    project_count: int = 0
    symbol_count: int = 0
    command_count: int = 0
    scan_duration_ms: float = 0.0
    language_distribution: dict[str, float] = field(default_factory=dict)


@dataclass(frozen=True)
class WorkspaceScanFailed(WorkspaceEvent):
    """Fired when a workspace scan fails."""

    scan_id: str = ""
    error: str = ""


# ---------------------------------------------------------------------------
# Planning events
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class PlanBuilt(WorkspaceEvent):
    """Fired when an execution plan is constructed."""

    plan_id: str = ""
    plan_title: str = ""
    step_count: int = 0
    plan_valid: bool = False
    risk_level: str = ""
    symbols_found: tuple[str, ...] = field(default_factory=tuple)


@dataclass(frozen=True)
class PlanValidationFailed(WorkspaceEvent):
    """Fired when plan validation finds errors."""

    plan_id: str = ""
    errors: tuple[str, ...] = field(default_factory=tuple)


@dataclass(frozen=True)
class PlanExecutionStarted(WorkspaceEvent):
    """Fired when plan execution begins."""

    plan_id: str = ""
    step_count: int = 0


@dataclass(frozen=True)
class PlanExecuted(WorkspaceEvent):
    """Fired when plan execution completes."""

    plan_id: str = ""
    success: bool = False
    steps_completed: int = 0
    steps_failed: int = 0
    total_duration_ms: float = 0.0
    error: str = ""


# ---------------------------------------------------------------------------
# Cache events
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class CacheHit(WorkspaceEvent):
    """Fired when a cache lookup succeeds."""

    cache_key: str = ""
    root_path: str = ""
    hit_count: int = 0


@dataclass(frozen=True)
class CacheMiss(WorkspaceEvent):
    """Fired when a cache lookup finds no entry."""

    cache_key: str = ""
    root_path: str = ""


@dataclass(frozen=True)
class CacheInvalidated(WorkspaceEvent):
    """Fired when cache is invalidated."""

    cache_key: str = ""
    reason: str = ""


# ---------------------------------------------------------------------------
# Symbol events
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class SymbolSearched(WorkspaceEvent):
    """Fired when a symbol search is performed."""

    query: str = ""
    results_count: int = 0
    search_duration_ms: float = 0.0


@dataclass(frozen=True)
class SymbolsIndexed(WorkspaceEvent):
    """Fired when symbols are extracted from a file."""

    file_path: str = ""
    symbol_count: int = 0
    language: str = ""
