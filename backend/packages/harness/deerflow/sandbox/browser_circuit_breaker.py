"""Per-thread circuit breaker for the browser subsystem.

Why
---
``run_browser_check`` calls a CDP-attached Chromium over the network.
When the chromium process dies (AIO container restart, OOM, network blip),
every concurrent caller hangs for the full ``wait_until='load'`` timeout
(20s) and then fails identically — cascading load on a dying subsystem.

The circuit breaker pattern (Nygard, Fowler, AWS Prescriptive Guidance)
short-circuits calls when failure-rate crosses a threshold, then
periodically probes for recovery. This file implements the canonical
3-state machine:

    CLOSED  ─(N failures in window)─▶  OPEN
       ▲                                │
       │                       (after cooldown)
       │                                ▼
       └──────(probe success)──  HALF_OPEN
                                       │
                                       ▼
                              (probe failure → back to OPEN)

States
------
- CLOSED: normal — every call goes through, failures count toward threshold.
- OPEN: degraded — calls raise ``BrowserCircuitOpenError`` immediately.
- HALF_OPEN: probing — one trial call is allowed; success → CLOSED, fail → OPEN.

Per-thread scope
----------------
Different threads (sandbox instances) are isolated: a Chromium crash on
``local:abc`` doesn't trip the circuit for ``local:xyz``. Matches the
existing per-thread lock granularity from browser_check.py.

Backwards compatibility
-----------------------
The breaker is opt-in: ``run_browser_check`` itself is unchanged.
Callers (observe_adjust_middleware, dev_verify, etc.) import
``guard_browser_call`` and wrap their check calls. Until they do, the
breaker is dormant.

Configuration (env vars)
------------------------
DEERFLOW_BROWSER_CIRCUIT_FAILURE_THRESHOLD   default 3
DEERFLOW_BROWSER_CIRCUIT_WINDOW_S            default 60.0
DEERFLOW_BROWSER_CIRCUIT_COOLDOWN_S          default 30.0
DEERFLOW_BROWSER_CIRCUIT_FORCE               admin override: open|close
"""

from __future__ import annotations

import logging
import os
import threading
import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Callable, TypeVar

from deerflow.sandbox.browser_errors import BrowserCircuitOpenError

logger = logging.getLogger(__name__)

# Defaults match the AWS / Fowler guidance for "short-lived service blip":
# 3 failures within 60s is enough to declare the subsystem degraded; 30s
# cooldown gives chromium time to recover without hammering it.
_FAILURE_THRESHOLD = int(os.environ.get("DEERFLOW_BROWSER_CIRCUIT_FAILURE_THRESHOLD", "3"))
_WINDOW_S = float(os.environ.get("DEERFLOW_BROWSER_CIRCUIT_WINDOW_S", "60.0"))
_COOLDOWN_S = float(os.environ.get("DEERFLOW_BROWSER_CIRCUIT_COOLDOWN_S", "30.0"))
_FORCE = os.environ.get("DEERFLOW_BROWSER_CIRCUIT_FORCE", "").strip().lower()


class CircuitState(str, Enum):
    CLOSED = "closed"
    OPEN = "open"
    HALF_OPEN = "half_open"


@dataclass
class _BreakerState:
    thread_id: str
    state: CircuitState = CircuitState.CLOSED
    # Sliding window of failure timestamps (seconds since epoch).
    failures: list[float] = field(default_factory=list)
    # When did the circuit last transition to OPEN?
    opened_at: float = 0.0
    # When did we last call the protected function? Used for HALF_OPEN throttle.
    last_probe_at: float = 0.0

    def prune_failures(self, now: float) -> None:
        """Drop failures older than the window."""
        cutoff = now - _WINDOW_S
        self.failures = [t for t in self.failures if t >= cutoff]


# One breaker per thread. Plain dict (NOT WeakValue) because the values
# are tiny (a few floats + an Enum) and WeakValue would let them be GC'd
# between calls when no strong reference is held locally — which silently
# resets the failure window. The total memory cost is bounded by the
# number of distinct thread_ids, which is per-sandbox and small.
_breakers: dict[str, _BreakerState] = {}
_breakers_meta = threading.Lock()  # guards insertion into _breakers


def _get_breaker(thread_id: str) -> _BreakerState:
    state = _breakers.get(thread_id)
    if state is not None:
        return state
    with _breakers_meta:
        state = _breakers.get(thread_id)
        if state is None:
            state = _BreakerState(thread_id=thread_id)
            _breakers[thread_id] = state
        return state


def get_circuit_state(thread_id: str) -> CircuitState:
    """Inspect the current state for ``thread_id`` (for /api/health)."""
    return _get_breaker(thread_id).state


def force_circuit(thread_id: str, state: CircuitState) -> None:
    """Admin: force a circuit into a specific state.

    Resets failure history and timestamps so a forced-open circuit
    has a clean cooldown window. Used by ops via env var or admin API.
    """
    br = _get_breaker(thread_id)
    logger.warning(
        "browser circuit force: thread_id=%s %s -> %s",
        thread_id,
        br.state.value,
        state.value,
    )
    br.state = state
    br.failures.clear()
    if state == CircuitState.OPEN:
        br.opened_at = time.monotonic()
    elif state == CircuitState.HALF_OPEN:
        br.last_probe_at = 0.0
    # CLOSED: nothing else to do


T = TypeVar("T")


def _apply_force_override(thread_id: str) -> CircuitState | None:
    """Return the forced state if DEERFLOW_BROWSER_CIRCUIT_FORCE applies, else None."""
    if not _FORCE:
        return None
    if _FORCE == "open":
        return CircuitState.OPEN
    if _FORCE == "close":
        return CircuitState.CLOSED
    # per-thread override: "open:local:abc" → force-open that thread only.
    prefix, _, tid = _FORCE.partition(":")
    if prefix in ("open", "close") and tid == thread_id:
        return CircuitState.OPEN if prefix == "open" else CircuitState.CLOSED
    return None


def _transition(br: _BreakerState, new_state: CircuitState, reason: str) -> None:
    if br.state == new_state:
        return
    logger.warning(
        "browser circuit transition: thread_id=%s %s -> %s (%s)",
        br.thread_id,
        br.state.value,
        new_state.value,
        reason,
    )
    br.state = new_state
    if new_state == CircuitState.OPEN:
        br.opened_at = time.monotonic()
        # Fresh OPEN: clear the previous probe timestamp so the next
        # HALF_OPEN transition (after cooldown) starts with a clean slate.
        br.last_probe_at = 0.0
    elif new_state == CircuitState.HALF_OPEN:
        # Allow the probe to run immediately on first HALF_OPEN entry.
        br.last_probe_at = 0.0
    elif new_state == CircuitState.CLOSED:
        br.failures.clear()


def _admit(br: _BreakerState, now: float) -> tuple[bool, float]:
    """Decide whether to admit a call. Returns ``(admitted, cooldown_remaining)``.

    ``cooldown_remaining`` is only meaningful when ``admitted`` is False.
    """
    forced = _apply_force_override(br.thread_id)
    if forced is not None:
        if forced == CircuitState.OPEN:
            remaining = max(0.0, _COOLDOWN_S - (now - br.opened_at))
            return (False, remaining)
        if forced == CircuitState.CLOSED:
            _transition(br, CircuitState.CLOSED, reason="force_close_env")
            return (True, 0.0)

    if br.state == CircuitState.CLOSED:
        return (True, 0.0)
    if br.state == CircuitState.OPEN:
        elapsed = now - br.opened_at
        if elapsed >= _COOLDOWN_S:
            _transition(br, CircuitState.HALF_OPEN, reason="cooldown_elapsed")
            # fall through to HALF_OPEN handling
        else:
            return (False, _COOLDOWN_S - elapsed)
    # HALF_OPEN: allow one probe at a time.
    if br.state == CircuitState.HALF_OPEN:
        if br.last_probe_at == 0.0 or (now - br.last_probe_at) > 0.5:
            br.last_probe_at = now
            return (True, 0.0)
        return (False, 0.5)
    return (True, 0.0)


def record_success(thread_id: str) -> None:
    """Record a successful call for ``thread_id``."""
    br = _get_breaker(thread_id)
    if br.state == CircuitState.HALF_OPEN:
        _transition(br, CircuitState.CLOSED, reason="probe_succeeded")
    # CLOSED: success doesn't reset the sliding window — that's what
    # the prune does naturally. We do clear failures on transition to
    # CLOSED above, so a fresh success in CLOSED leaves history intact.


def record_failure(thread_id: str) -> None:
    """Record a failed call for ``thread_id``."""
    br = _get_breaker(thread_id)
    now = time.monotonic()
    if br.state == CircuitState.HALF_OPEN:
        _transition(br, CircuitState.OPEN, reason="probe_failed")
        br.opened_at = now
        return
    br.prune_failures(now)
    br.failures.append(now)
    if len(br.failures) >= _FAILURE_THRESHOLD and br.state == CircuitState.CLOSED:
        _transition(br, CircuitState.OPEN, reason=f"threshold_reached({len(br.failures)})")
        br.opened_at = now


def guard_browser_call(
    thread_id: str,
    fn: Callable[..., T],
    *args: Any,
    **kwargs: Any,
) -> T:
    """Execute ``fn`` under the per-thread circuit breaker.

    Behaviour:
      - CLOSED: run normally, record success/failure, raise on error.
      - HALF_OPEN: run one probe, transition to CLOSED/OPEN based on outcome.
      - OPEN: raise ``BrowserCircuitOpenError`` immediately.

    The exception types are the canonical B4 hierarchy:
      - ``fn`` raises → propagated, recorded as failure.
      - circuit OPEN → ``BrowserCircuitOpenError(cooldown_remaining_s=...)``.
    """
    br = _get_breaker(thread_id)
    admitted, cooldown = _admit(br, time.monotonic())
    if not admitted:
        raise BrowserCircuitOpenError(
            f"browser circuit OPEN for thread_id={thread_id}; retry in {cooldown:.1f}s",
            cooldown_remaining_s=cooldown,
            context={"thread_id": thread_id, "cooldown_remaining_s": cooldown},
        )
    try:
        result = fn(*args, **kwargs)
    except Exception:
        record_failure(thread_id)
        raise
    else:
        record_success(thread_id)
        return result


# Admin / introspection helpers (used by /api/health and tests)
def reset_all_circuits() -> None:
    """Reset every per-thread breaker to CLOSED + clear failure history.

    Used by tests for isolation. Crucial: we MUST clear failures even
    when state is already CLOSED, otherwise stale failures from a
    previous test pollute the failure window for the next test and
    cause spurious circuit-trips.

    ``_transition`` is for state-CHANGE events and returns early when
    the target state equals the current state — that's the wrong
    primitive here. We explicitly set state + clear failures below.
    """
    with _breakers_meta:
        for state in list(_breakers.values()):
            prev = state.state
            state.state = CircuitState.CLOSED
            state.failures.clear()
            state.opened_at = 0.0
            state.last_probe_at = 0.0
            if prev != CircuitState.CLOSED:
                logger.warning(
                    "browser circuit force-reset: thread_id=%s %s -> closed",
                    state.thread_id,
                    prev.value,
                )


def snapshot() -> dict[str, str]:
    """Return {thread_id: state_value} for all known breakers."""
    return {tid: br.state.value for tid, br in _breakers.items()}