"""Typed models for the Nova Execution Kernel.

Phase C7 — immutable dataclasses describing everything that flows through
the kernel: requests, results, audit records, resource limits, and the
status vocabulary.  No I/O lives here.

Usage::

    from deerflow.execution.models import ExecutionRequest, ExecutionClass

    req = ExecutionRequest(
        argv=["git", "-C", "/repo", "status", "--porcelain"],
        execution_class=ExecutionClass.GIT,
        timeout=8.0,
        correlation_id="corr-1",
    )
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import Enum
from typing import Any


def _now_iso() -> str:
    return datetime.now(UTC).isoformat()


def _new_execution_id() -> str:
    return uuid.uuid4().hex


# ---------------------------------------------------------------------------
# Vocabulary
# ---------------------------------------------------------------------------


class ExecutionClass(str, Enum):
    """Adapter/workload class an execution belongs to.

    Drives policy selection and resource pools.  One class per adapter.
    """

    SHELL = "shell"
    DOCKER = "docker"
    GIT = "git"
    BROWSER = "browser"
    PYTHON = "python"
    PM2 = "pm2"
    SYSTEMD = "systemd"


class ExecutionStatus(str, Enum):
    """Phase C8 execution lifecycle states.

    Implements the complete execution state machine with enforced transition
    rules. Impossible transitions raise InvalidExecutionStateTransition.
    """

    # Pre-execution
    PENDING = "pending"
    ALLOCATED = "allocated"  # Admitted, slot acquired
    PREPARING = "preparing"   # Pre-exec setup (cwd, env, PTY)

    # Active execution
    RUNNING = "running"           # Process executing
    WAITING_INPUT = "waiting_input"  # Interactive session waiting for input
    STREAMING = "streaming"          # Output being streamed

    # Cancellation in progress
    CANCELLING = "cancelling"  # Cancellation in progress
    STOPPING = "stopping"      # Graceful shutdown in progress

    # Terminal states
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    TIMED_OUT = "timed_out"
    CANCELLED = "cancelled"
    STOPPED = "stopped"           # Confirmed stopped by kernel
    DENIED = "denied"             # Policy/resource rejected

    # Zombie states (Phase C8)
    ZOMBIE_DETECTED = "zombie_detected"  # Process alive, owner missing
    REAPED = "reaped"                    # Zombie has been reaped

    @property
    def is_terminal(self) -> bool:
        return self in (
            ExecutionStatus.SUCCEEDED,
            ExecutionStatus.FAILED,
            ExecutionStatus.TIMED_OUT,
            ExecutionStatus.CANCELLED,
            ExecutionStatus.STOPPED,
            ExecutionStatus.DENIED,
            ExecutionStatus.REAPED,
        )

    @property
    def is_active(self) -> bool:
        return self in (
            ExecutionStatus.ALLOCATED,
            ExecutionStatus.PREPARING,
            ExecutionStatus.RUNNING,
            ExecutionStatus.WAITING_INPUT,
            ExecutionStatus.STREAMING,
        )

    @property
    def is_cancellable(self) -> bool:
        return self in (
            ExecutionStatus.PENDING,
            ExecutionStatus.ALLOCATED,
            ExecutionStatus.PREPARING,
            ExecutionStatus.RUNNING,
            ExecutionStatus.WAITING_INPUT,
            ExecutionStatus.STREAMING,
        )


# ---------------------------------------------------------------------------
# Request
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ResourceLimits:
    """Per-request resource constraints enforced by the kernel.

    Phase C8 adds execution budget fields: max_depth, max_children, max_recursion.
    These prevent runaway execution chains (e.g. infinite repository scans).
    """

    timeout: float = 60.0
    queue_timeout: float = 30.0
    max_output_bytes: int = 10 * 1024 * 1024
    grace_period: float = 5.0

    # Phase C8: execution budget
    max_depth: int = 10        # Max command chain depth (prevents infinite recursion)
    max_children: int = 100    # Max child processes per parent
    max_recursion: int = 5     # Max nested execution levels
    heartbeat_interval: float = 5.0  # Seconds between heartbeat emissions


@dataclass(frozen=True)
class ExecutionRequest:
    """A single, fully-described execution.

    ``argv`` is always an explicit argument vector — the kernel never
    invokes a shell implicitly (``shell=True`` is banned by construction).
    Shell semantics are expressed explicitly as ``["/bin/sh", "-c", cmd]``
    by the Shell adapter.

    Phase C8 adds: parent_execution_id, session_id.
    """

    argv: tuple[str, ...] = ()
    execution_class: ExecutionClass = ExecutionClass.SHELL
    execution_id: str = field(default_factory=_new_execution_id)
    cwd: str | None = None
    env: dict[str, str] | None = None  # None → inherit parent environment
    stdin: str | None = None
    limits: ResourceLimits = field(default_factory=ResourceLimits)
    correlation_id: str = ""
    run_id: str = ""
    thread_id: str = ""
    parent_execution_id: str = ""  # Phase C8: parent execution in the tree
    session_id: str = ""            # Phase C8: associated shell session
    intent: str = ""  # human-readable purpose, recorded in the audit trail
    labels: dict[str, str] = field(default_factory=dict)
    created_at: str = field(default_factory=_now_iso)

    def __post_init__(self) -> None:
        if isinstance(self.argv, list):  # tolerate list input, store tuple
            object.__setattr__(self, "argv", tuple(self.argv))
        # Phase C8: also support parent_execution_id via labels for backward compat
        if self.parent_execution_id:
            object.__setattr__(
                self, "labels", {**self.labels, "parent_execution_id": self.parent_execution_id}
            )


# ---------------------------------------------------------------------------
# Result
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ExecutionResult:
    """Immutable outcome of one execution."""

    execution_id: str
    status: ExecutionStatus
    exit_code: int | None = None
    stdout: str = ""
    stderr: str = ""
    duration_ms: float = 0.0
    started_at: str = ""
    finished_at: str = ""
    error: str | None = None
    execution_class: ExecutionClass = ExecutionClass.SHELL
    correlation_id: str = ""

    @property
    def ok(self) -> bool:
        return self.status is ExecutionStatus.SUCCEEDED

    @property
    def output(self) -> str:
        """Combined stdout + stderr (stdout first)."""
        if self.stdout and self.stderr:
            return f"{self.stdout}\n{self.stderr}"
        return self.stdout or self.stderr


# ---------------------------------------------------------------------------
# Policy decision
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class PolicyDecision:
    """Outcome of a policy evaluation for a request."""

    allowed: bool
    reason: str = ""
    effective_timeout: float | None = None  # policy-clamped timeout


# ---------------------------------------------------------------------------
# Audit record
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ExecutionRecord:
    """One audit-trail entry — request summary + result summary.

    Environment variables are never stored (only their key names) so the
    audit trail cannot leak secrets.
    """

    execution_id: str
    execution_class: str
    argv: tuple[str, ...]
    status: str
    exit_code: int | None
    duration_ms: float
    intent: str = ""
    cwd: str | None = None
    env_keys: tuple[str, ...] = ()
    correlation_id: str = ""
    run_id: str = ""
    thread_id: str = ""
    started_at: str = ""
    finished_at: str = ""
    error: str = ""
    seq: int = 0
    record_hash: str = ""
    prev_hash: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "execution_id": self.execution_id,
            "execution_class": self.execution_class,
            "argv": list(self.argv),
            "status": self.status,
            "exit_code": self.exit_code,
            "duration_ms": self.duration_ms,
            "intent": self.intent,
            "cwd": self.cwd,
            "env_keys": list(self.env_keys),
            "correlation_id": self.correlation_id,
            "run_id": self.run_id,
            "thread_id": self.thread_id,
            "started_at": self.started_at,
            "finished_at": self.finished_at,
            "error": self.error,
            "seq": self.seq,
            "record_hash": self.record_hash,
            "prev_hash": self.prev_hash,
        }
