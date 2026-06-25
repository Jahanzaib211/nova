"""Typed exception hierarchy for the browser/computer control surface.

Enterprise-grade error handling requires structured exception types so callers
can react specifically to transient vs permanent failures, vs configuration /
circuit-open conditions. This module defines the hierarchy used by:

  - browser_check.py (browser self-test)
  - browser_check_concurrency.py (bounded concurrency)
  - community/browserless/browserless_client.py (HTTP fallback)
  - tools/builtins/workspace_tools.py (browser_navigate/click/input/eval)

Hierarchy::

    BrowserError(Exception)
      ├── BrowserTransientError       # network glitch, CDP hiccup → retry
      │     ├── BrowserTimeoutError   # explicit timeout (also in concurrency module)
      │     └── BrowserConnectionError
      ├── BrowserPermanentError       # 404 / invalid selector → fail fast
      ├── BrowserUnavailableError     # no sandbox, no chromium → degraded mode
      └── BrowserCircuitOpenError     # circuit breaker tripped → fail fast

Backwards compatibility
-----------------------
``BrowserTimeoutError`` is re-exported from ``browser_check_concurrency``
to keep one canonical class. Existing bare ``except Exception`` blocks
continue to work — they just won't get the new structured information.
New code should catch these typed exceptions explicitly.
"""

from __future__ import annotations


class BrowserError(Exception):
    """Base for all browser/computer-control failures.

    Carries an optional ``context`` dict so callers can attach thread_id,
    route, sandbox_id, etc. without changing the exception signature.
    """

    def __init__(self, message: str = "", *, context: dict | None = None) -> None:
        super().__init__(message)
        self.message = message
        self.context: dict = dict(context) if context else {}

    def with_context(self, **kwargs) -> "BrowserError":
        """Return self with additional context fields merged in.

        Idempotent and safe to chain::

            raise BrowserTransientError("cdp hiccup").with_context(
                thread_id=thread_id, cdp_url=cdp_url,
            )
        """
        self.context.update({k: v for k, v in kwargs.items() if v is not None})
        return self

    def __str__(self) -> str:
        if self.context:
            return f"{self.message} | context={self.context}"
        return self.message


class BrowserTransientError(BrowserError):
    """A failure that's expected to clear on its own — retry is appropriate.

    Examples: CDP connection reset, websocket disconnect mid-page, short-lived
    sandbox restart. Should map to a retry with backoff in B3.
    """


class BrowserConnectionError(BrowserTransientError):
    """Network-layer failure reaching the browser (CDP websocket, HTTP)."""


class BrowserTimeoutError(BrowserTransientError):
    """Operation exceeded its time budget.

    Used for both per-page render timeouts and total wall-clock budgets.
    Re-exported from browser_check_concurrency for one canonical class.
    """


class BrowserPermanentError(BrowserError):
    """A failure that won't clear on retry — fail fast.

    Examples: invalid CSS selector, 404 on a known endpoint, malformed URL,
    selector matched zero elements. Should NOT be retried.
    """


class BrowserUnavailableError(BrowserError):
    """The browser subsystem itself is unavailable.

    Examples: local sandbox has no chromium, AIO sandbox not provisioned,
    manifest entry disabled. Caller should fall back to a non-browser
    strategy or surface a clean "no browser" reason to the UI.
    """


class BrowserCircuitOpenError(BrowserError):
    """Per-thread circuit breaker is OPEN — calls are short-circuited.

    NOT a failure of the underlying call, but a deliberate decision to
    stop hammering a degraded subsystem. Carries ``cooldown_remaining_s``
    so callers can render a meaningful "retry in Ns" message.
    """

    def __init__(self, message: str = "", *, cooldown_remaining_s: float = 0.0, context: dict | None = None) -> None:
        super().__init__(message, context=context)
        self.cooldown_remaining_s = float(cooldown_remaining_s)


# Sentinel helpers for classify-on-catch patterns — keep this small.
TRANSIENT_EXCEPTIONS: tuple[type[BaseException], ...] = (
    BrowserTransientError,
    BrowserConnectionError,
    BrowserTimeoutError,
)
PERMANENT_EXCEPTIONS: tuple[type[BaseException], ...] = (BrowserPermanentError,)


def is_transient(exc: BaseException) -> bool:
    """True if ``exc`` is a browser error that warrants retry."""
    return isinstance(exc, TRANSIENT_EXCEPTIONS)


def is_permanent(exc: BaseException) -> bool:
    """True if ``exc`` is a browser error that should fail fast."""
    return isinstance(exc, PERMANENT_EXCEPTIONS)