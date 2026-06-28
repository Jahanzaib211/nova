"""Bounded retry with exponential backoff + jitter.

Implements the AWS Prescriptive Guidance "Retry with backoff" pattern,
combined with the Stripe-style jitter to prevent thundering herd.

Only retries TRANSIENT errors. Permanent errors (4xx, invalid selector,
bad URL) fail-fast — they will never recover, so retrying wastes the
caller's time and the subsystem's resources.

Co-operates with the circuit breaker (browser_circuit_breaker.py): if
the circuit is OPEN, retry is short-circuited immediately.

Usage
-----
::

    from deerflow.sandbox.browser_retry import retry_browser_call

    result = retry_browser_call(
        thread_id=thread_id,
        fn=lambda: client.browser_page.navigate(url=url, wait_until="load"),
        operation="browser_navigate",
    )

Schedule (default)
-----------------
  attempt 1: try immediately
  attempt 2: wait 100ms ± 30% jitter
  attempt 3: wait 300ms ± 30% jitter

Total max wait ~ 400ms — short enough to not block the agent loop,
long enough to ride out typical transient blips.

Exceptions
----------
  - transient (B4) → retried
  - permanent (B4) → fail-fast
  - circuit OPEN → fail-fast with BrowserCircuitOpenError
  - other unexpected → fail-fast (don't mask unknown bugs)

Backwards compatibility
-----------------------
This module is purely additive. Existing callers continue to work
without retries. Opt in by importing ``retry_browser_call``.
"""

from __future__ import annotations

import asyncio
import logging
import random
import time
from collections.abc import Awaitable, Callable
from typing import Any, TypeVar

from deerflow.sandbox.browser_circuit_breaker import (
    get_circuit_state,
    guard_browser_call,
)
from deerflow.sandbox.browser_errors import (
    BrowserCircuitOpenError,
    BrowserError,
    is_permanent,
)

logger = logging.getLogger(__name__)

# Default schedule: 2 retries with exponential backoff (100ms, 300ms) +
# ±30% jitter. Matches Stripe's "be a good distributed citizen" guidance
# while staying short enough not to block the agent loop.
DEFAULT_BASE_BACKOFF_MS = 100
DEFAULT_MAX_BACKOFF_MS = 5000
DEFAULT_JITTER_FRACTION = 0.30
DEFAULT_MAX_ATTEMPTS = 3  # initial + 2 retries


T = TypeVar("T")


def _compute_backoff_ms(
    attempt: int,
    *,
    base_ms: int = DEFAULT_BASE_BACKOFF_MS,
    jitter_fraction: float = DEFAULT_JITTER_FRACTION,
) -> float:
    """Exponential backoff with jitter.

    ``attempt`` is 1-indexed (1 = before first retry). Returns the
    number of milliseconds to sleep BEFORE that attempt.
    """
    raw = base_ms * (2 ** (attempt - 1))
    jitter = raw * jitter_fraction
    return max(0.0, raw + random.uniform(-jitter, jitter))


def retry_browser_call(
    thread_id: str,
    fn: Callable[..., T],
    *args: Any,
    max_attempts: int = DEFAULT_MAX_ATTEMPTS,
    base_backoff_ms: int = DEFAULT_BASE_BACKOFF_MS,
    max_backoff_ms: int = DEFAULT_MAX_BACKOFF_MS,
    jitter_fraction: float = DEFAULT_JITTER_FRACTION,
    operation: str | None = None,
    on_retry: Callable[[int, float, BaseException], None] | None = None,
    **kwargs: Any,
) -> T:
    """Call ``fn`` under bounded retry + circuit breaker.

    Args:
        thread_id: scope key for circuit-breaker state.
        fn: callable to invoke. May be sync.
        max_attempts: total attempts including the first try. 1 = no retry.
        base_backoff_ms: first-retry backoff in ms (default 100).
        max_backoff_ms: hard cap on per-attempt backoff (default 5000).
        jitter_fraction: random spread around the nominal backoff (default 0.30).
        operation: label for logs / context. Optional but recommended.
        on_retry: callback ``(attempt, sleep_ms, exc)`` fired BEFORE each retry sleep.
        **kwargs: forwarded to ``fn``.

    Returns:
        The value returned by ``fn`` on the first successful attempt.

    Raises:
        BrowserPermanentError: fail-fast on permanent failures.
        BrowserCircuitOpenError: fail-fast when circuit OPEN.
        The original exception: if max_attempts is exhausted or the
        exception is not a transient BrowserError.
    """
    if max_attempts < 1:
        raise ValueError(f"max_attempts must be >= 1, got {max_attempts}")

    op_label = operation or getattr(fn, "__name__", "browser_call")

    last_exc: BaseException | None = None
    for attempt in range(1, max_attempts + 1):
        # Pre-check: if circuit is OPEN, don't even try.
        # The breaker also enforces this inside guard_browser_call, but
        # checking here saves the (small) cost of acquiring the lock + the
        # circuit lookup, and gives a cleaner log path.
        if get_circuit_state(thread_id).value == "open":
            raise BrowserCircuitOpenError(
                f"retry_browser_call short-circuited for thread_id={thread_id} during operation={op_label}",
                cooldown_remaining_s=0.0,  # actual cooldown computed inside guard
            )

        try:
            return guard_browser_call(thread_id, fn, *args, **kwargs)
        except Exception as exc:
            last_exc = exc

            # 1. Circuit OPEN → fail fast, no retry.
            if isinstance(exc, BrowserCircuitOpenError):
                logger.warning(
                    "retry_browser_call: circuit OPEN during %s (thread_id=%s); no retry",
                    op_label,
                    thread_id,
                )
                raise

            # 2. Permanent error → fail fast, no retry.
            if is_permanent(exc):
                logger.info(
                    "retry_browser_call: permanent error during %s (thread_id=%s): %s; no retry",
                    op_label,
                    thread_id,
                    exc,
                )
                raise

            # 3. Non-browser unexpected exception → don't mask unknown bugs.
            if not isinstance(exc, BrowserError):
                logger.warning(
                    "retry_browser_call: non-browser exception during %s (thread_id=%s): %s; no retry",
                    op_label,
                    thread_id,
                    exc,
                )
                raise

            # 4. Transient browser error AND attempts remain → retry.
            if attempt < max_attempts:
                sleep_ms = _compute_backoff_ms(
                    attempt,
                    base_ms=base_backoff_ms,
                    jitter_fraction=jitter_fraction,
                )
                sleep_ms = min(sleep_ms, max_backoff_ms)
                logger.info(
                    "retry_browser_call: transient error during %s (thread_id=%s, attempt %d/%d): %s; sleeping %.1fms then retrying",
                    op_label,
                    thread_id,
                    attempt,
                    max_attempts,
                    exc,
                    sleep_ms,
                )
                if on_retry is not None:
                    try:
                        on_retry(attempt, sleep_ms, exc)
                    except Exception:  # callback bugs must not affect retry
                        logger.debug("retry_browser_call: on_retry callback raised", exc_info=True)
                # Sleep is per-retry, not per-call. Using time.sleep here is
                # fine because retry_browser_call runs in worker threads
                # (asyncio.to_thread) for the callsites that matter.
                time.sleep(sleep_ms / 1000.0)
                continue

            # 5. Transient + attempts exhausted → fail with last exception.
            logger.warning(
                "retry_browser_call: exhausted %d attempts for %s (thread_id=%s); last error: %s",
                max_attempts,
                op_label,
                thread_id,
                exc,
            )
            raise

    # Unreachable, but the type checker wants it.
    assert last_exc is not None
    raise last_exc


async def retry_browser_call_async(
    thread_id: str,
    fn: Callable[..., Awaitable[T]],
    *args: Any,
    max_attempts: int = DEFAULT_MAX_ATTEMPTS,
    base_backoff_ms: int = DEFAULT_BASE_BACKOFF_MS,
    max_backoff_ms: int = DEFAULT_MAX_BACKOFF_MS,
    jitter_fraction: float = DEFAULT_JITTER_FRACTION,
    operation: str | None = None,
    **kwargs: Any,
) -> T:
    """Async-friendly version of ``retry_browser_call``.

    Retries use ``asyncio.sleep`` so the event loop stays responsive.
    The circuit breaker (``guard_browser_call``) is sync because the
    state is a simple in-memory dict under a lock — it's safe to call
    from async code, just don't await it.
    """
    if max_attempts < 1:
        raise ValueError(f"max_attempts must be >= 1, got {max_attempts}")

    op_label = operation or getattr(fn, "__name__", "browser_call_async")

    for attempt in range(1, max_attempts + 1):
        if get_circuit_state(thread_id).value == "open":
            raise BrowserCircuitOpenError(
                f"retry_browser_call_async short-circuited for thread_id={thread_id} during operation={op_label}",
                cooldown_remaining_s=0.0,
            )
        try:
            # Circuit-breaker guard wraps the awaited call. We use the sync
            # guard to check + record + run, then await separately — the
            # guard returns the coroutine without awaiting it.
            coro = guard_browser_call(thread_id, fn, *args, **kwargs)
            return await coro
        except Exception as exc:
            if isinstance(exc, BrowserCircuitOpenError):
                raise
            if is_permanent(exc) or not isinstance(exc, BrowserError):
                raise
            if attempt < max_attempts:
                sleep_ms = min(
                    _compute_backoff_ms(attempt, base_ms=base_backoff_ms, jitter_fraction=jitter_fraction),
                    max_backoff_ms,
                )
                logger.info(
                    "retry_browser_call_async: transient during %s (thread_id=%s, attempt %d/%d): %s; sleeping %.1fms",
                    op_label,
                    thread_id,
                    attempt,
                    max_attempts,
                    exc,
                    sleep_ms,
                )
                await asyncio.sleep(sleep_ms / 1000.0)
                continue
            raise
