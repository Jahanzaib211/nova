"""Scheduler for the Nova Execution Kernel.

Phase C8 — admission control.  The scheduler is the single gate between a
request and a running process: it applies the policy engine, then acquires
a resource slot, then enforces execution budget limits.

Phase C8 additionally provides:

- Execution depth tracking per run_id (prevents infinite command chains).
- Per-parent child count enforcement (limits fan-out).
- Recursion depth enforcement (prevents unbounded nested execution).

The result is a typed :class:`Admission` the kernel acts on deterministically —
no process is ever created for a rejected request.
"""

from __future__ import annotations

import logging
import threading
from collections import defaultdict
from dataclasses import dataclass, field

from deerflow.execution.models import ExecutionRequest
from deerflow.execution.policy import PolicyEngine
from deerflow.execution.resources import ResourceManager

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class Admission:
    """Outcome of scheduling one request."""

    admitted: bool
    reason: str = ""
    effective_timeout: float = 60.0
    # True when a resource slot was acquired and must be released.
    slot_held: bool = False


class DepthTracker:
    """Tracks execution depth per run_id for budget enforcement.

    Phase C8: prevents infinite command chains from runaway repository scans.
    Thread-safe for concurrent access from multiple threads.
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._depths: dict[str, int] = defaultdict(int)  # run_id → depth

    def enter(self, run_id: str) -> int:
        """Increment and return the new depth for run_id."""
        with self._lock:
            self._depths[run_id] += 1
            return self._depths[run_id]

    def exit(self, run_id: str) -> int:
        """Decrement and return the new depth for run_id."""
        with self._lock:
            current = self._depths.get(run_id, 0)
            if current > 0:
                self._depths[run_id] = current - 1
            else:
                self._depths[run_id] = 0
            return self._depths[run_id]

    def get_depth(self, run_id: str) -> int:
        """Get current depth for run_id."""
        with self._lock:
            return self._depths.get(run_id, 0)

    def reset(self, run_id: str) -> None:
        """Reset depth for run_id (e.g., when run completes)."""
        with self._lock:
            self._depths.pop(run_id, None)


class ChildTracker:
    """Tracks child count per parent execution for budget enforcement.

    Phase C8: limits fan-out from parent executions to prevent resource exhaustion.
    Thread-safe for concurrent access.
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._children: dict[str, int] = defaultdict(int)  # parent_eid → count

    def add_child(self, parent_execution_id: str) -> int:
        """Increment and return new child count for parent."""
        with self._lock:
            self._children[parent_execution_id] += 1
            return self._children[parent_execution_id]

    def remove_child(self, parent_execution_id: str) -> int:
        """Decrement and return new child count for parent."""
        with self._lock:
            current = self._children.get(parent_execution_id, 0)
            if current > 0:
                self._children[parent_execution_id] = current - 1
            return self._children[parent_execution_id]

    def count(self, parent_execution_id: str) -> int:
        """Get child count for parent."""
        with self._lock:
            return self._children.get(parent_execution_id, 0)


class Scheduler:
    """Policy + resource + budget admission for the kernel.

    Phase C8: enforces execution depth limits, child count limits, and
    recursion limits in addition to policy and resource checks.
    """

    def __init__(self, policy_engine: PolicyEngine, resource_manager: ResourceManager) -> None:
        self._policy = policy_engine
        self._resources = resource_manager
        self._depth_tracker = DepthTracker()
        self._child_tracker = ChildTracker()

    def admit(self, request: ExecutionRequest) -> Admission:
        # Phase 1: Policy check
        decision = self._policy.evaluate(request)
        if not decision.allowed:
            logger.warning(
                "Execution %s denied by policy: %s (argv=%s)",
                request.execution_id,
                decision.reason,
                list(request.argv[:3]),
            )
            return Admission(admitted=False, reason=f"policy: {decision.reason}")

        # Phase C8: Execution budget — depth check
        if request.run_id:
            max_depth = request.limits.max_depth
            current_depth = self._depth_tracker.get_depth(request.run_id)
            if current_depth >= max_depth:
                logger.warning(
                    "Execution %s denied: depth limit reached (%s/%s) run=%s",
                    request.execution_id,
                    current_depth,
                    max_depth,
                    request.run_id,
                )
                return Admission(
                    admitted=False,
                    reason=f"execution budget: depth limit {current_depth}/{max_depth}",
                )

        # Phase C8: Execution budget — child count check
        parent_eid = request.labels.get("parent_execution_id", "") or request.parent_execution_id
        if parent_eid:
            max_children = request.limits.max_children
            child_count = self._child_tracker.count(parent_eid)
            if child_count >= max_children:
                logger.warning(
                    "Execution %s denied: child limit reached (%s/%s) parent=%s",
                    request.execution_id,
                    child_count,
                    max_children,
                    parent_eid,
                )
                return Admission(
                    admitted=False,
                    reason=f"execution budget: child limit {child_count}/{max_children}",
                )

        # Phase 2: Resource acquisition
        acquired = self._resources.acquire(
            request.execution_class, request.limits.queue_timeout
        )
        if not acquired:
            logger.warning(
                "Execution %s denied: resource saturation for class %s",
                request.execution_id,
                request.execution_class.value,
            )
            return Admission(
                admitted=False,
                reason=f"resources: class {request.execution_class.value} saturated",
            )

        # Phase C8: Increment counters after successful admission
        if request.run_id:
            self._depth_tracker.enter(request.run_id)
        if parent_eid:
            self._child_tracker.add_child(parent_eid)

        return Admission(
            admitted=True,
            effective_timeout=decision.effective_timeout or request.limits.timeout,
            slot_held=True,
        )

    def release(self, request: ExecutionRequest) -> None:
        self._resources.release(request.execution_class)

        # Phase C8: Decrement counters
        if request.run_id:
            self._depth_tracker.exit(request.run_id)
        parent_eid = request.labels.get("parent_execution_id", "") or request.parent_execution_id
        if parent_eid:
            self._child_tracker.remove_child(parent_eid)

    def get_depth(self, run_id: str) -> int:
        """Get current execution depth for a run."""
        return self._depth_tracker.get_depth(run_id)

    def get_child_count(self, parent_execution_id: str) -> int:
        """Get current child count for a parent execution."""
        return self._child_tracker.count(parent_execution_id)

    def reset_run(self, run_id: str) -> None:
        """Reset all budget tracking for a run (called when run completes)."""
        self._depth_tracker.reset(run_id)
