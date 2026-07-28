"""Canonical lifecycle model for Nova runs.

Phase C3 — one canonical lifecycle that every subsystem references.
Existing enums (RunStatus, SubagentStatus, CircuitState) remain for
backward compatibility; RunLifecycleStatus is the canonical
representation used by the event bus and service layer.

Usage::

    from deerflow.runtime.lifecycle import RunLifecycleStatus, adapt_run_status

    # Create from existing RunStatus
    lifecycle = adapt_run_status(run_status)

    # Or create directly
    lifecycle = RunLifecycleStatus.RUNNING
"""

from __future__ import annotations

from enum import Enum


class RunLifecycleStatus(str, Enum):
    """Canonical lifecycle status for agent runs.

    This is the single source of truth for run state.  All subsystems
    should eventually reference this enum instead of maintaining their
    own lifecycle representations.

    States:
        CREATED: Run allocated, not yet initialized.
        INITIALIZING: Run setting up workspace, sandbox, etc.
        RUNNING: Agent actively processing.
        CHECKPOINT: Run hit a checkpoint (LangGraph persistence).
        PAUSED: Run paused by user or system.
        RESUMED: Run resumed after pause.
        RECOVERING: Run recovering from a failure.
        COMPLETED: Run finished successfully.
        FAILED: Run failed with an error.
        CANCELLED: Run cancelled by user or system.
        ARCHIVED: Run archived after completion.
    """

    CREATED = "created"
    INITIALIZING = "initializing"
    RUNNING = "running"
    CHECKPOINT = "checkpoint"
    PAUSED = "paused"
    RESUMED = "resumed"
    RECOVERING = "recovering"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"
    ARCHIVED = "archived"

    @property
    def is_terminal(self) -> bool:
        """Return True for states that represent run completion."""
        return self in {
            RunLifecycleStatus.COMPLETED,
            RunLifecycleStatus.FAILED,
            RunLifecycleStatus.CANCELLED,
            RunLifecycleStatus.ARCHIVED,
        }

    @property
    def is_active(self) -> bool:
        """Return True for states where the run is actively processing."""
        return self in {
            RunLifecycleStatus.RUNNING,
            RunLifecycleStatus.INITIALIZING,
            RunLifecycleStatus.RECOVERING,
        }

    @property
    def is_transitional(self) -> bool:
        """Return True for states that are temporary transitions."""
        return self in {
            RunLifecycleStatus.CHECKPOINT,
            RunLifecycleStatus.PAUSED,
            RunLifecycleStatus.RESUMED,
        }


# ---------------------------------------------------------------------------
# Adapters from existing enums
# ---------------------------------------------------------------------------


def adapt_run_status(status: str | Enum) -> RunLifecycleStatus:
    """Adapt an existing RunStatus or string to RunLifecycleStatus.

    This provides backward compatibility during the migration period.
    Existing code that produces RunStatus values will continue to work.
    """
    val = status.value if hasattr(status, "value") else str(status)
    val = val.lower().strip()

    mapping = {
        "pending": RunLifecycleStatus.CREATED,
        "running": RunLifecycleStatus.RUNNING,
        "success": RunLifecycleStatus.COMPLETED,
        "error": RunLifecycleStatus.FAILED,
        "timeout": RunLifecycleStatus.FAILED,
        "interrupted": RunLifecycleStatus.CANCELLED,
        # SubagentStatus mappings
        "completed": RunLifecycleStatus.COMPLETED,
        "failed": RunLifecycleStatus.FAILED,
        "cancelled": RunLifecycleStatus.CANCELLED,
        "timed_out": RunLifecycleStatus.FAILED,
        # Direct lifecycle status passthrough
        "created": RunLifecycleStatus.CREATED,
        "initializing": RunLifecycleStatus.INITIALIZING,
        "checkpoint": RunLifecycleStatus.CHECKPOINT,
        "paused": RunLifecycleStatus.PAUSED,
        "resumed": RunLifecycleStatus.RESUMED,
        "recovering": RunLifecycleStatus.RECOVERING,
        "archived": RunLifecycleStatus.ARCHIVED,
    }

    return mapping.get(val, RunLifecycleStatus.RUNNING)


def to_run_status(lifecycle: RunLifecycleStatus) -> str:
    """Convert RunLifecycleStatus back to the legacy RunStatus string.

    This allows existing code that reads RunStatus to continue working
    during the migration period.
    """
    reverse_mapping = {
        RunLifecycleStatus.CREATED: "pending",
        RunLifecycleStatus.INITIALIZING: "running",
        RunLifecycleStatus.RUNNING: "running",
        RunLifecycleStatus.CHECKPOINT: "running",
        RunLifecycleStatus.PAUSED: "interrupted",
        RunLifecycleStatus.RESUMED: "running",
        RunLifecycleStatus.RECOVERING: "running",
        RunLifecycleStatus.COMPLETED: "success",
        RunLifecycleStatus.FAILED: "error",
        RunLifecycleStatus.CANCELLED: "interrupted",
        RunLifecycleStatus.ARCHIVED: "success",
    }
    return reverse_mapping.get(lifecycle, "running")
