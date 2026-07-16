"""Execution Plan — structured plan produced by the Workspace Planner.

Phase C9 — every execution begins with a plan.  Plans are
typed, validated, and traceable.  No raw shell calls without a plan.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any


class RiskLevel(str, Enum):
    """Risk level of an execution plan or step."""

    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"

    @property
    def order(self) -> int:
        """Integer order for ``max()`` comparisons.

        ``max()`` on a ``str, Enum`` uses alphabetical string ordering,
        which gives wrong results (e.g. ``max(LOW, HIGH) == LOW``).
        Use ``.order`` for severity comparisons instead.
        """
        return _RISK_ORDER[self]


_RISK_ORDER: dict[RiskLevel, int] = {
    RiskLevel.LOW: 0,
    RiskLevel.MEDIUM: 1,
    RiskLevel.HIGH: 2,
    RiskLevel.CRITICAL: 3,
}


class StepKind(str, Enum):
    """Kind of execution step."""

    READ = "read"
    WRITE = "write"
    EDIT = "edit"
    DELETE = "delete"
    CREATE = "create"
    RUN_COMMAND = "run_command"
    RUN_TEST = "run_test"
    RUN_BUILD = "run_build"
    RUN_MIGRATION = "run_migration"
    START_SERVICE = "start_service"
    STOP_SERVICE = "stop_service"
    RESTART_SERVICE = "restart_service"
    DEPLOY = "deploy"
    ROLLBACK = "rollback"
    CONFIRM = "confirm"
    ASK_USER = "ask_user"


@dataclass(frozen=True)
class ExecutionStep:
    """A single step in an execution plan."""

    step_id: str
    kind: StepKind
    description: str
    argv: tuple[str, ...] = field(default_factory=tuple)
    cwd: str = ""
    env: dict[str, str] = field(default_factory=dict)
    affected_files: tuple[str, ...] = field(default_factory=tuple)
    rollback_step_id: str = ""
    risk_level: RiskLevel = RiskLevel.LOW
    reasoning: str = ""
    confidence: float = 1.0
    can_undo: bool = True
    is_destructive: bool = False
    is_system_wide: bool = False
    timeout_seconds: float = 60.0
    metadata: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        object.__setattr__(self, "kind", StepKind(self.kind))
        object.__setattr__(self, "risk_level", RiskLevel(self.risk_level))


@dataclass(frozen=True)
class ExecutionPlan:
    """A complete execution plan for a user goal.

    Produced by ``WorkspacePlanner`` before any execution begins.
    Validated by ``PlanValidator`` before being passed to the Execution Kernel.
    """

    plan_id: str
    goal: str
    steps: tuple[ExecutionStep, ...]
    affected_projects: tuple[str, ...] = field(default_factory=tuple)
    affected_files: tuple[str, ...] = field(default_factory=tuple)
    risk_level: RiskLevel = RiskLevel.LOW
    reasoning: str = ""
    total_steps: int = 0
    estimated_duration_seconds: float = 0.0
    rollback_plan: tuple[str, ...] = field(default_factory=tuple)
    confidence: float = 1.0
    warnings: tuple[str, ...] = field(default_factory=tuple)
    planner_version: str = "0.1.0"
    metadata: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        object.__setattr__(self, "risk_level", RiskLevel(self.risk_level))
        object.__setattr__(self, "total_steps", len(self.steps))

    @property
    def is_safe(self) -> bool:
        return self.risk_level in (RiskLevel.LOW, RiskLevel.MEDIUM)
