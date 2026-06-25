"""Concurrency primitives for browser_check.

Provides bounded concurrency (asyncio.Semaphore) and total-timeout wrappers
for ``run_browser_check`` callsites so the run loop is protected from:

  - Burst overload: 100 concurrent present_files events fanning out to
    browser_check would spawn 100 Playwright sessions and OOM the gateway.
    The semaphore caps the in-flight check count.

  - Unbounded hang: ``run_browser_check`` itself has no upper bound on
    runtime — a hung CDP connection can wedge a thread indefinitely.
    The timeout wrapper bounds the wait and surfaces a clean structured
    error event so the UI badge shows the failure mode correctly.

This module is intentionally additive (v7+). ``run_browser_check`` itself
is unchanged; ``observe_adjust_middleware`` and any future callsite use
these helpers to opt into bounded behaviour.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import os
from typing import Any

logger = logging.getLogger(__name__)

# Module-level semaphore for ``run_browser_check`` callsites. Acquired BEFORE
# scheduling the thread to bound the in-flight check count across the gateway.
# Default of 4 matches the budget one chromium context can comfortably drive
# on a single gateway host; override via env var for deployments with more RAM
# or where the underlying AIO container manages its own queue.
MAX_CONCURRENT_BROWSER_CHECKS = int(os.environ.get("DEERFLOW_MAX_CONCURRENT_BROWSER_CHECKS", "4"))

# When the semaphore is saturated, callers wait at most this long before
# giving up and surfacing a structured error. 10s is long enough to ride
# out short bursts (a typical check takes 3-6s) but short enough to fail
# fast when saturation is sustained.
_BROWSER_CHECK_QUEUE_TIMEOUT_S = float(os.environ.get("DEERFLOW_BROWSER_CHECK_QUEUE_TIMEOUT_S", "10.0"))

# Total wall-clock timeout for a single ``run_browser_check`` call. This is
# a hard ceiling that includes the queue wait PLUS the actual check. 30s is
# well above the typical 5-15s check + queue wait, but short enough to fail
# fast on truly hung CDP sessions.
_BROWSER_CHECK_TOTAL_TIMEOUT_S = float(os.environ.get("DEERFLOW_BROWSER_CHECK_TOTAL_TIMEOUT_S", "30.0"))

_BROWSER_CHECK_SEMAPHORE = asyncio.Semaphore(MAX_CONCURRENT_BROWSER_CHECKS)


def get_browser_check_semaphore() -> asyncio.Semaphore:
    """Return the process-wide ``asyncio.Semaphore`` for ``run_browser_check``.

    Exposed for tests that want to inspect saturation or override the limit.
    """
    return _BROWSER_CHECK_SEMAPHORE


class BrowserCheckSaturatedError(Exception):
    """Raised when the semaphore is full and the queue timeout elapses.

    Callers should treat this as a transient failure: surface a structured
    event (``status='saturation'``) and let the agent retry on the next
    tool cycle rather than aborting the whole run.
    """

    def __init__(self, waited_s: float, capacity: int) -> None:
        super().__init__(
            f"browser_check saturated after waiting {waited_s:.1f}s (capacity={capacity}); "
            "consider raising DEERFLOW_MAX_CONCURRENT_BROWSER_CHECKS"
        )
        self.waited_s = waited_s
        self.capacity = capacity


class BrowserCheckTimeoutError(Exception):
    """Raised when ``run_browser_check`` exceeds the total wall-clock budget.

    This is distinct from a Playwright CDP timeout inside the check (which
    already produces a ``RouteResult(status='unreachable')``): this fires
    when even the queue-acquisition phase plus check execution exceeds the
    budget, indicating a deeper hang (gateway-side event loop blocked,
    chromium stuck, etc.).
    """

    def __init__(self, total_budget_s: float) -> None:
        super().__init__(
            f"browser_check exceeded total wall-clock budget of {total_budget_s:.1f}s"
        )
        self.total_budget_s = total_budget_s


async def run_browser_check_bounded(
    thread_id: str,
    sandbox: Any,
    *,
    label: str = "app",
    routes: list[str] | None = None,
    with_screenshot: bool = True,
    total_timeout_s: float | None = None,
    queue_timeout_s: float | None = None,
) -> Any:
    """Run ``run_browser_check`` with bounded concurrency + total timeout.

    Behaviour:
      1. Acquire the process-wide semaphore (queues if saturated).
      2. Run ``run_browser_check`` in a worker thread (``asyncio.to_thread``).
      3. Bound the whole flow by ``total_timeout_s``; on timeout, cancel and
         raise :class:`BrowserCheckTimeoutError`.
      4. If queue acquisition exceeds ``queue_timeout_s``, raise
         :class:`BrowserCheckSaturatedError` immediately without running.

    Returns the ``BrowserCheck`` from ``run_browser_check`` unchanged.

    The semaphore is always released, even on error or timeout.
    """
    # Local imports keep this module side-effect-free at import time.
    from deerflow.sandbox.browser_check import run_browser_check

    total = total_timeout_s if total_timeout_s is not None else _BROWSER_CHECK_TOTAL_TIMEOUT_S
    queue = queue_timeout_s if queue_timeout_s is not None else _BROWSER_CHECK_QUEUE_TIMEOUT_S

    loop = asyncio.get_running_loop()
    deadline = loop.time() + total

    # Phase 1: acquire the semaphore within ``queue`` seconds.
    try:
        await asyncio.wait_for(
            _BROWSER_CHECK_SEMAPHORE.acquire(),
            timeout=queue,
        )
    except asyncio.TimeoutError as e:
        waited = queue
        logger.warning(
            "browser_check saturated (waited %.1fs, capacity=%d) for thread_id=%s",
            waited,
            MAX_CONCURRENT_BROWSER_CHECKS,
            thread_id,
        )
        raise BrowserCheckSaturatedError(waited_s=waited, capacity=MAX_CONCURRENT_BROWSER_CHECKS) from e

    # From here on we MUST release the semaphore, even on error.
    try:
        # Phase 2: run the check in a thread with the remaining budget.
        remaining = max(0.0, deadline - loop.time())
        try:
            return await asyncio.wait_for(
                asyncio.to_thread(
                    run_browser_check,
                    thread_id,
                    sandbox,
                    label=label,
                    routes=routes,
                    with_screenshot=with_screenshot,
                ),
                timeout=remaining,
            )
        except asyncio.TimeoutError as e:
            logger.warning(
                "browser_check exceeded total budget of %.1fs for thread_id=%s",
                total,
                thread_id,
            )
            raise BrowserCheckTimeoutError(total_budget_s=total) from e
    finally:
        with contextlib.suppress(ValueError):
            # ValueError if already released (shouldn't happen, but be defensive).
            _BROWSER_CHECK_SEMAPHORE.release()