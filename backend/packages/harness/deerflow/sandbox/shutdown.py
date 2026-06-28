"""Graceful shutdown for the browser/computer control surface.

On SIGTERM (12-Factor disposability: "Maximize robustness with fast
startup and graceful shutdown"), the gateway has at most ``_SHUTDOWN_BUDGET_S``
seconds to:
  1. Drain the bounded-concurrency semaphore (wait for in-flight checks).
  2. Flush any cached Playwright sessions.
  3. Reset / clear idempotency caches (so a fresh process starts cold).
  4. Reset all circuit breakers (so the next process doesn't inherit
     a stuck-OPEN state from this one).

Each step is bounded — we never block shutdown indefinitely. Failures
in any single step are logged but don't prevent subsequent steps from
running.

Install with::

    from deerflow.sandbox.shutdown import install_shutdown_hooks
    install_shutdown_hooks()

The installer is idempotent — calling it twice is a no-op.

For tests, ``run_shutdown()`` is exposed directly so the lifecycle can
be exercised without sending real signals.
"""

from __future__ import annotations

import atexit
import logging
import os
import signal
import threading
import time
from typing import Any

logger = logging.getLogger(__name__)

# Default budget matches AWS's "shutdown budget" guidance: enough to drain
# typical in-flight work, short enough to satisfy orchestrator timeouts
# (Kubernetes default terminationGracePeriodSeconds=30).
_SHUTDOWN_BUDGET_S = float(os.environ.get("DEERFLOW_SHUTDOWN_BUDGET_S", "5.0"))

# Module-level state to make shutdown idempotent.
_installed = False
_shutdown_lock = threading.Lock()
_shutdown_completed = False


def _drain_browser_check_semaphore(budget_s: float) -> None:
    """Wait up to ``budget_s`` for the bounded semaphore to drain.

    The semaphore in browser_check_concurrency.py has no drain API, so
    we poll _value until it hits the cap (meaning no permits in use)
    or the budget elapses.
    """
    try:
        from deerflow.sandbox.browser_check_concurrency import (
            MAX_CONCURRENT_BROWSER_CHECKS,
            get_browser_check_semaphore,
        )
    except ImportError:
        return

    sem = get_browser_check_semaphore()
    cap = MAX_CONCURRENT_BROWSER_CHECKS
    deadline = time.monotonic() + budget_s
    while time.monotonic() < deadline:
        if sem._value >= cap:  # type: ignore[attr-defined]
            logger.info("shutdown: browser_check semaphore drained (capacity=%d)", cap)
            return
        time.sleep(0.05)
    logger.warning(
        "shutdown: browser_check semaphore did not drain within %.1fs (capacity=%d, in_use=%d)",
        budget_s,
        cap,
        cap - sem._value,  # type: ignore[attr-defined]
    )


def _flush_browser_resources(budget_s: float) -> None:
    """Best-effort cleanup of Playwright + CDP sessions.

    The browser_check module manages its own ``sync_playwright`` context
    via contextlib, so individual sessions are short-lived. We don't have
    a global registry to flush — but we give Playwright a chance to
    garbage-collect any in-flight sync_playwright() instances by running
    the gc.
    """
    import gc

    deadline = time.monotonic() + budget_s
    while time.monotonic() < deadline:
        gc.collect()
        time.sleep(0.05)
    logger.debug("shutdown: browser resource flush complete")


def _reset_browser_state() -> None:
    """Reset transient browser state so the next process starts cold.

    Idempotency cache, circuit breakers, and the last-checks dict are
    all in-memory state that has no business surviving a process restart.
    """
    try:
        from deerflow.tools.builtins.workspace_tools import (
            _browser_navigate_idempotency,
        )

        _browser_navigate_idempotency.clear()
        logger.info("shutdown: cleared browser_navigate idempotency cache")
    except ImportError:
        pass

    try:
        from deerflow.sandbox import browser_circuit_breaker as cb

        cb.reset_all_circuits()
        logger.info("shutdown: reset all circuit breakers")
    except ImportError:
        pass

    try:
        from deerflow.sandbox import browser_check as bc

        with bc._LAST_CHECKS_LOCK:
            bc._last_checks.clear()
        logger.info("shutdown: cleared last browser_check results")
    except (ImportError, AttributeError):
        pass


def run_shutdown(budget_s: float | None = None) -> dict[str, Any]:
    """Execute the full shutdown sequence.

    Returns a dict describing what happened (for /api/health observability
    and for tests). Always runs to completion, never raises.

    Idempotent: a second call returns the cached result without re-running.
    """
    global _shutdown_completed

    budget = budget_s if budget_s is not None else _SHUTDOWN_BUDGET_S

    with _shutdown_lock:
        if _shutdown_completed:
            logger.debug("shutdown: already completed; returning cached result")
            return _last_result()

        start = time.monotonic()
        log: dict[str, Any] = {
            "started_at": start,
            "budget_s": budget,
            "steps": {},
        }

        # Step 1: drain in-flight browser_check work.
        t = time.monotonic()
        try:
            _drain_browser_semaphore_safe(budget * 0.6)
            log["steps"]["drain_semaphore"] = {
                "ok": True,
                "elapsed_ms": round((time.monotonic() - t) * 1000, 1),
            }
        except Exception as e:
            log["steps"]["drain_semaphore"] = {"ok": False, "error": str(e)}

        # Step 2: flush Playwright sessions.
        t = time.monotonic()
        try:
            _flush_browser_resources_safe(budget * 0.2)
            log["steps"]["flush_resources"] = {
                "ok": True,
                "elapsed_ms": round((time.monotonic() - t) * 1000, 1),
            }
        except Exception as e:
            log["steps"]["flush_resources"] = {"ok": False, "error": str(e)}

        # Step 3: reset transient state.
        t = time.monotonic()
        try:
            _reset_browser_state_safe()
            log["steps"]["reset_state"] = {
                "ok": True,
                "elapsed_ms": round((time.monotonic() - t) * 1000, 1),
            }
        except Exception as e:
            log["steps"]["reset_state"] = {"ok": False, "error": str(e)}

        log["total_elapsed_ms"] = round((time.monotonic() - start) * 1000, 1)
        log["completed_at"] = time.monotonic()

        _LAST_RESULT["value"] = log
        _shutdown_completed = True
        logger.info(
            "shutdown: completed in %.1fms (budget %.1fs)",
            log["total_elapsed_ms"],
            budget,
        )
        return log


# ---------- safe wrappers (any individual failure must not block others) ----------


def _drain_browser_semaphore_safe(budget_s: float) -> None:
    _drain_browser_check_semaphore(budget_s)


def _flush_browser_resources_safe(budget_s: float) -> None:
    _flush_browser_resources(budget_s)


def _reset_browser_state_safe() -> None:
    _reset_browser_state()


_LAST_RESULT: dict[str, Any] = {"value": None}


def _last_result() -> dict[str, Any]:
    return _LAST_RESULT["value"] or {"completed": False}


def reset_for_testing() -> None:
    """Reset shutdown state for tests. Production code never calls this."""
    global _shutdown_completed, _LAST_RESULT
    _shutdown_completed = False
    _LAST_RESULT = {"value": None}


def install_shutdown_hooks() -> bool:
    """Register atexit + SIGTERM/SIGINT handlers.

    Returns True on first install, False on subsequent (no-op) calls.
    """
    global _installed
    with _shutdown_lock:
        if _installed:
            return False
        _installed = True

    # atexit runs on normal interpreter shutdown — covers `python -c "..."`
    # and clean process exits. It does NOT run on SIGKILL.
    atexit.register(_safe_shutdown)

    # SIGTERM / SIGINT handlers — best-effort. Uvicorn installs its own
    # signal handlers in many deployments; ours is additive (we run alongside).
    def _signal_handler(signum: int, frame: Any) -> None:
        logger.info("shutdown: received signal %d; running shutdown", signum)
        _safe_shutdown()

    for sig in (signal.SIGTERM, signal.SIGINT):
        try:
            signal.signal(sig, _signal_handler)
        except (ValueError, OSError):
            # Not in main thread / unsupported on this platform — skip silently.
            pass

    return True


def _safe_shutdown() -> None:
    """atexit / signal-safe entrypoint. Catches and logs everything."""
    try:
        run_shutdown()
    except Exception:
        logger.exception("shutdown: unexpected error in _safe_shutdown")
