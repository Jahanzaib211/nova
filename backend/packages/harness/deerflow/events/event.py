"""Domain events for the Nova event bus.

Phase C3 — immutable frozen dataclasses representing domain events.
Every event carries correlation_id, run_id, thread_id, and timestamp
for full traceability.

Usage::

    from deerflow.events.event import RunCreated, DomainEvent

    event = RunCreated(
        run_id="abc123",
        thread_id="t1",
        correlation_id="corr1",
        payload={"model": "gpt-4"},
    )
    assert isinstance(event, DomainEvent)
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any


def _now_iso() -> str:
    return datetime.now(UTC).isoformat()


def _new_event_id() -> str:
    return uuid.uuid4().hex


# ---------------------------------------------------------------------------
# Base event
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class DomainEvent:
    """Base class for all domain events.

    All events are immutable and carry standard metadata for tracing.
    Subclasses add event-specific payload fields.
    """

    event_id: str = field(default_factory=_new_event_id)
    correlation_id: str = ""
    run_id: str = ""
    thread_id: str = ""
    timestamp: str = field(default_factory=_now_iso)
    payload: dict[str, Any] = field(default_factory=dict)

    @property
    def event_type(self) -> str:
        """Return the event type name (class name)."""
        return type(self).__name__


# ---------------------------------------------------------------------------
# Run lifecycle events
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class RunCreated(DomainEvent):
    """Emitted when a run is allocated but not yet initialized."""


@dataclass(frozen=True)
class RunInitialized(DomainEvent):
    """Emitted when a run has finished setup (workspace, sandbox)."""


@dataclass(frozen=True)
class RunStarted(DomainEvent):
    """Emitted when a run begins active processing."""


@dataclass(frozen=True)
class RunCheckpointCreated(DomainEvent):
    """Emitted when a run hits a LangGraph checkpoint."""


@dataclass(frozen=True)
class RunPaused(DomainEvent):
    """Emitted when a run is paused by user or system."""


@dataclass(frozen=True)
class RunResumed(DomainEvent):
    """Emitted when a run resumes after pause."""


@dataclass(frozen=True)
class RunRecovering(DomainEvent):
    """Emitted when a run begins recovery from failure."""


@dataclass(frozen=True)
class RunCompleted(DomainEvent):
    """Emitted when a run finishes successfully."""


@dataclass(frozen=True)
class RunFailed(DomainEvent):
    """Emitted when a run fails with an error."""


@dataclass(frozen=True)
class RunCancelled(DomainEvent):
    """Emitted when a run is cancelled by user or system."""


@dataclass(frozen=True)
class RunArchived(DomainEvent):
    """Emitted when a run is archived after completion."""


# ---------------------------------------------------------------------------
# Workspace events
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class WorkspaceMounted(DomainEvent):
    """Emitted when a thread's workspace directories are created."""


@dataclass(frozen=True)
class WorkspaceReleased(DomainEvent):
    """Emitted when a thread's workspace is released."""


# ---------------------------------------------------------------------------
# Browser events
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class BrowserStarted(DomainEvent):
    """Emitted when a browser preview session starts."""


@dataclass(frozen=True)
class BrowserStopped(DomainEvent):
    """Emitted when a browser preview session stops."""


# ---------------------------------------------------------------------------
# Health events
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class HealthChanged(DomainEvent):
    """Emitted when a health probe result changes state."""


# ---------------------------------------------------------------------------
# Tool events
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ToolExecuted(DomainEvent):
    """Emitted when a tool finishes execution."""


# ---------------------------------------------------------------------------
# Artifact events
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ArtifactCreated(DomainEvent):
    """Emitted when an artifact is created or updated."""
