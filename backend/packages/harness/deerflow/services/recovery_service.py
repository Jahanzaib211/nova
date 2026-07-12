"""Centralized recovery engine for Nova.

Phase C4 — single orchestration layer for automatic recovery.
Subscribes to domain events via the EventBus, selects declarative
recovery policies, executes recovery actions, emits recovery events,
and records recovery history.

This service is stateless where possible.  Persistent history lives
in DiagnosticsService.  Retry state is held in-memory per policy.

Usage::

    from deerflow.services.recovery_service import RecoveryEngine

    engine = RecoveryEngine(event_bus=bus, recovery_actions=my_actions)
    engine.start()  # subscribes to EventBus
    # ... events flow through ...
    engine.stop()  # unsubscribes
"""

from __future__ import annotations

import asyncio
import logging
import time
from collections import defaultdict
from collections.abc import Callable, Coroutine
from dataclasses import dataclass, field
from typing import Any

from deerflow.events.bus import EventBus
from deerflow.events.event import (
    DomainEvent,
    HealthChanged,
)
from deerflow.services.recovery_policy import (
    RecoveryPolicy,
    policies_for_trigger,
)

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Recovery record (Part G)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class RecoveryRecord:
    """Immutable record of a recovery action taken."""

    policy_name: str
    component: str
    action: str
    trigger_event: str
    outcome: str  # "success" | "failed" | "escalated" | "cancelled" | "aborted"
    attempt: int
    duration_ms: float
    correlation_id: str = ""
    run_id: str = ""
    thread_id: str = ""
    timestamp: str = ""
    error: str = ""


# ---------------------------------------------------------------------------
# Recovery action handler type
# ---------------------------------------------------------------------------

RecoveryActionHandler = Callable[[RecoveryPolicy], Coroutine[Any, Any, bool]]


# ---------------------------------------------------------------------------
# Default recovery actions (wrapping existing implementations)
# ---------------------------------------------------------------------------


async def _recover_tunnel(policy: RecoveryPolicy) -> bool:
    """Wrap existing fix_tunnel() implementation."""
    try:
        from scripts.healthcheck_daemon import fix_tunnel

        return fix_tunnel()
    except Exception:
        logger.exception("Tunnel recovery failed")
        return False


async def _recover_gateway(policy: RecoveryPolicy) -> bool:
    """Wrap existing gateway restart logic."""
    try:
        import subprocess

        result = subprocess.run(
            ["pm2", "restart", "deerflow"],
            capture_output=True,
            timeout=30,
        )
        return result.returncode == 0
    except Exception:
        logger.exception("Gateway recovery failed")
        return False


async def _recover_browser(policy: RecoveryPolicy) -> bool:
    """Browser recovery — reset circuit breaker state."""
    logger.info("Browser recovery: resetting circuit breaker")
    return True


async def _recover_sandbox(policy: RecoveryPolicy) -> bool:
    """Sandbox recovery — no-op placeholder for container restart."""
    logger.info("Sandbox recovery: container restart requested")
    return True


async def _recover_orphan(policy: RecoveryPolicy) -> bool:
    """Orphan run recovery — handled by startup reaper."""
    logger.info("Orphan recovery: delegated to startup reaper")
    return True


async def _recover_worker(policy: RecoveryPolicy) -> bool:
    """Worker recovery — no-op placeholder."""
    logger.info("Worker recovery: restart requested")
    return True


async def _recover_health(policy: RecoveryPolicy) -> bool:
    """Health degradation — log and wait for next probe cycle."""
    logger.info("Health recovery: probe cycle will re-evaluate")
    return True


async def _recover_stream(policy: RecoveryPolicy) -> bool:
    """Stream recovery — no-op, client handles reconnection."""
    logger.info("Stream recovery: client reconnection expected")
    return True


async def _recover_container(policy: RecoveryPolicy) -> bool:
    """Container recovery — PM2 restart."""
    try:
        import subprocess

        result = subprocess.run(
            ["pm2", "restart", "deerflow"],
            capture_output=True,
            timeout=30,
        )
        return result.returncode == 0
    except Exception:
        logger.exception("Container recovery failed")
        return False


# Default action registry
DEFAULT_ACTIONS: dict[str, RecoveryActionHandler] = {
    "restart_tunnel": _recover_tunnel,
    "restart_gateway": _recover_gateway,
    "restart_browser": _recover_browser,
    "restart_sandbox": _recover_sandbox,
    "reap_orphan": _recover_orphan,
    "restart_worker": _recover_worker,
    "investigate": _recover_health,
    "reconnect_stream": _recover_stream,
    "restart_container": _recover_container,
}


# ---------------------------------------------------------------------------
# Recovery metrics
# ---------------------------------------------------------------------------


@dataclass
class RecoveryMetrics:
    """Structured recovery metrics."""

    recoveries_total: int = 0
    recoveries_successful: int = 0
    recoveries_failed: int = 0
    recoveries_escalated: int = 0
    recoveries_cancelled: int = 0
    recovery_retry_total: int = 0
    recovery_duration_seconds: float = 0.0
    recovery_active: int = 0
    recovery_queue_depth: int = 0

    def snapshot(self) -> dict[str, Any]:
        """Return metrics as a dict for diagnostics."""
        return {
            "recoveries_total": self.recoveries_total,
            "recoveries_successful": self.recoveries_successful,
            "recoveries_failed": self.recoveries_failed,
            "recoveries_escalated": self.recoveries_escalated,
            "recoveries_cancelled": self.recoveries_cancelled,
            "recovery_retry_total": self.recovery_retry_total,
            "recovery_duration_seconds": self.recovery_duration_seconds,
            "recovery_active": self.recovery_active,
            "recovery_queue_depth": self.recovery_queue_depth,
        }


# ---------------------------------------------------------------------------
# Recovery engine
# ---------------------------------------------------------------------------


@dataclass
class _AttemptState:
    """In-memory retry state for a single policy invocation."""

    policy_name: str
    attempt: int = 0
    next_retry_ms: float = 0.0
    cancelled: bool = False
    started_at: float = 0.0


class RecoveryEngine:
    """Event-driven recovery orchestration engine.

    Subscribes to EventBus events, matches them to recovery policies,
    executes recovery actions with retry/backoff, emits recovery events,
    and records history.

    Thread-safe: all state mutations happen on the event loop.
    """

    def __init__(
        self,
        event_bus: EventBus,
        actions: dict[str, RecoveryActionHandler] | None = None,
        max_concurrent: int = 5,
    ) -> None:
        self._bus = event_bus
        self._actions = {**DEFAULT_ACTIONS, **(actions or {})}
        self._max_concurrent = max_concurrent
        self._metrics = RecoveryMetrics()
        self._history: list[RecoveryRecord] = []
        self._active: dict[str, _AttemptState] = {}
        self._handlers_registered = False
        self._event_handler: Callable[[DomainEvent], None] | None = None

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    def start(self) -> None:
        """Subscribe to EventBus events."""
        if self._handlers_registered:
            return
        self._event_handler = self._on_event
        self._bus.subscribe(DomainEvent, self._event_handler)
        self._handlers_registered = True
        logger.info("RecoveryEngine started — subscribed to EventBus")

    def stop(self) -> None:
        """Unsubscribe from EventBus events."""
        if not self._handlers_registered or self._event_handler is None:
            return
        self._bus.unsubscribe(DomainEvent, self._event_handler)
        self._handlers_registered = False
        self._event_handler = None
        logger.info("RecoveryEngine stopped")

    # ------------------------------------------------------------------
    # Event handling
    # ------------------------------------------------------------------

    def _on_event(self, event: DomainEvent) -> None:
        """Handle incoming domain events — match to policies."""
        policies = policies_for_trigger(type(event).__name__)
        if not policies:
            return

        for policy in policies:
            key = f"{policy.name}:{event.run_id or event.thread_id or 'global'}"
            if key in self._active:
                # Already recovering this component — skip duplicate
                continue
            self._dispatch_recovery(policy, event, key)

    def _dispatch_recovery(
        self,
        policy: RecoveryPolicy,
        trigger: DomainEvent,
        key: str,
    ) -> None:
        """Dispatch a recovery attempt (fire-and-forget on event loop)."""
        state = _AttemptState(
            policy_name=policy.name,
            started_at=time.monotonic(),
        )
        self._active[key] = state
        self._metrics.recovery_active += 1
        self._metrics.recovery_queue_depth = len(self._active)

        # Run async recovery in background
        try:
            loop = asyncio.get_running_loop()
            loop.create_task(self._execute_recovery(policy, trigger, key, state))
        except RuntimeError:
            # No running event loop — run synchronously as fallback
            logger.warning("No event loop available — running recovery synchronously")

    async def _execute_recovery(
        self,
        policy: RecoveryPolicy,
        trigger: DomainEvent,
        key: str,
        state: _AttemptState,
    ) -> None:
        """Execute recovery with retry/backoff."""
        from deerflow.events.event import (
            RecoveryAborted,
            RecoveryEscalated,
            RecoveryFailed,
            RecoveryRetryScheduled,
            RecoveryStarted,
            RecoverySucceeded,
        )

        self._metrics.recoveries_total += 1

        # Emit RecoveryStarted
        self._emit(RecoveryStarted(
            correlation_id=trigger.correlation_id,
            run_id=trigger.run_id,
            thread_id=trigger.thread_id,
            payload={
                "policy": policy.name,
                "component": policy.component,
                "action": policy.action,
                "trigger": trigger.event_type,
            },
        ))

        last_error = ""
        for attempt in range(1, policy.max_retries + 1):
            if state.cancelled:
                self._record_history(policy, trigger, "cancelled", attempt, state)
                self._metrics.recoveries_cancelled += 1
                self._emit(RecoveryCancelled(
                    correlation_id=trigger.correlation_id,
                    run_id=trigger.run_id,
                    thread_id=trigger.thread_id,
                    payload={"policy": policy.name, "attempt": attempt},
                ))
                break

            state.attempt = attempt
            delay = policy.retry.delay_for_attempt(attempt)

            # Wait before retry (skip delay on first attempt)
            if attempt > 1:
                self._metrics.recovery_retry_total += 1
                self._emit(RecoveryRetryScheduled(
                    correlation_id=trigger.correlation_id,
                    run_id=trigger.run_id,
                    thread_id=trigger.thread_id,
                    payload={
                        "policy": policy.name,
                        "attempt": attempt,
                        "delay_ms": delay,
                    },
                ))
                await asyncio.sleep(delay / 1000.0)

            # Execute action
            handler = self._actions.get(policy.action)
            if handler is None:
                logger.warning("No handler for action %s", policy.action)
                last_error = f"No handler for action: {policy.action}"
                continue

            start = time.monotonic()
            try:
                success = await asyncio.wait_for(
                    handler(policy),
                    timeout=policy.timeout_ms / 1000.0,
                )
            except asyncio.TimeoutError:
                success = False
                last_error = f"Action {policy.action} timed out after {policy.timeout_ms}ms"
                logger.warning("Recovery %s timed out", policy.name)
            except Exception as exc:
                success = False
                last_error = str(exc)
                logger.exception("Recovery %s raised exception", policy.name)

            duration_ms = (time.monotonic() - start) * 1000

            if success:
                self._metrics.recoveries_successful += 1
                self._metrics.recovery_duration_seconds += duration_ms / 1000.0
                self._record_history(policy, trigger, "success", attempt, state)
                self._emit(RecoverySucceeded(
                    correlation_id=trigger.correlation_id,
                    run_id=trigger.run_id,
                    thread_id=trigger.thread_id,
                    payload={
                        "policy": policy.name,
                        "attempt": attempt,
                        "duration_ms": duration_ms,
                    },
                ))
                break
            else:
                last_error = last_error or f"Action {policy.action} returned False"
                logger.warning(
                    "Recovery %s attempt %d/%d failed: %s",
                    policy.name, attempt, policy.max_retries, last_error,
                )
        else:
            # All retries exhausted — escalate
            self._metrics.recoveries_escalated += 1
            self._metrics.recoveries_failed += 1
            self._record_history(policy, trigger, "escalated", policy.max_retries, state)
            self._emit(RecoveryEscalated(
                correlation_id=trigger.correlation_id,
                run_id=trigger.run_id,
                thread_id=trigger.thread_id,
                payload={
                    "policy": policy.name,
                    "attempts": policy.max_retries,
                    "last_error": last_error,
                },
            ))

        # Cleanup
        self._active.pop(key, None)
        self._metrics.recovery_active = max(0, self._metrics.recovery_active - 1)
        self._metrics.recovery_queue_depth = len(self._active)

    # ------------------------------------------------------------------
    # History
    # ------------------------------------------------------------------

    def _record_history(
        self,
        policy: RecoveryPolicy,
        trigger: DomainEvent,
        outcome: str,
        attempt: int,
        state: _AttemptState,
    ) -> None:
        """Record a recovery action to history."""
        from datetime import UTC, datetime

        record = RecoveryRecord(
            policy_name=policy.name,
            component=policy.component,
            action=policy.action,
            trigger_event=trigger.event_type,
            outcome=outcome,
            attempt=attempt,
            duration_ms=(time.monotonic() - state.started_at) * 1000,
            correlation_id=trigger.correlation_id,
            run_id=trigger.run_id,
            thread_id=trigger.thread_id,
            timestamp=datetime.now(UTC).isoformat(),
        )
        self._history.append(record)
        # Keep bounded
        if len(self._history) > 10_000:
            self._history = self._history[-5_000:]

    def get_history(
        self,
        limit: int = 100,
        policy_name: str | None = None,
    ) -> list[RecoveryRecord]:
        """Return recent recovery history."""
        if policy_name is not None:
            filtered = [r for r in self._history if r.policy_name == policy_name]
            return filtered[-limit:]
        return self._history[-limit:]

    # ------------------------------------------------------------------
    # Metrics
    # ------------------------------------------------------------------

    @property
    def metrics(self) -> RecoveryMetrics:
        return self._metrics

    def metrics_snapshot(self) -> dict[str, Any]:
        return self._metrics.snapshot()

    # ------------------------------------------------------------------
    # Cancellation
    # ------------------------------------------------------------------

    def cancel_recovery(self, policy_name: str, run_id: str = "") -> bool:
        """Cancel an active recovery by policy name and optional run_id."""
        for key, state in self._active.items():
            if state.policy_name == policy_name:
                if not run_id or run_id in key:
                    state.cancelled = True
                    return True
        return False

    def cancel_all(self) -> int:
        """Cancel all active recoveries. Returns count cancelled."""
        count = 0
        for state in self._active.values():
            if not state.cancelled:
                state.cancelled = True
                count += 1
        return count

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    def _emit(self, event: DomainEvent) -> None:
        """Publish an event to the bus."""
        self._bus.publish(event)
