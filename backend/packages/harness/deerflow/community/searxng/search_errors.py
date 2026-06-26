"""Typed exception hierarchy for the search/research subsystem.

Mirrors the browser_errors.py pattern for consistency. Hierarchy::

    SearchError(Exception)
      ├── SearchTransientError        # network glitch, rate limit → retry
      │     ├── SearchTimeoutError    # explicit timeout
      │     └── SearchConnectionError # network unreachable
      ├── SearchPermanentError        # invalid query, 404 → fail fast
      ├── SearchUnavailableError      # no search backend available
      └── SearchCircuitOpenError      # circuit breaker tripped → fail fast
"""

from __future__ import annotations


class SearchError(Exception):
    """Base for all search/research failures."""

    def __init__(self, message: str = "", *, context: dict | None = None) -> None:
        super().__init__(message)
        self.message = message
        self.context: dict = dict(context) if context else {}

    def with_context(self, **kwargs) -> "SearchError":
        self.context.update({k: v for k, v in kwargs.items() if v is not None})
        return self

    def __str__(self) -> str:
        if self.context:
            return f"{self.message} | context={self.context}"
        return self.message


class SearchTransientError(SearchError):
    """A failure expected to clear on retry — network glitch, rate limit."""


class SearchConnectionError(SearchTransientError):
    """Network-layer failure reaching the search backend."""


class SearchTimeoutError(SearchTransientError):
    """Operation exceeded its time budget."""


class SearchPermanentError(SearchError):
    """A failure that won't clear on retry — invalid query, 404."""


class SearchUnavailableError(SearchError):
    """No search backend is available — caller should use fallback."""


class SearchCircuitOpenError(SearchError):
    """Circuit breaker is OPEN — calls short-circuited."""

    def __init__(
        self, message: str = "", *, cooldown_remaining_s: float = 0.0, context: dict | None = None
    ) -> None:
        super().__init__(message, context=context)
        self.cooldown_remaining_s = float(cooldown_remaining_s)


TRANSIENT_EXCEPTIONS: tuple[type[BaseException], ...] = (
    SearchTransientError,
    SearchConnectionError,
    SearchTimeoutError,
)
PERMANENT_EXCEPTIONS: tuple[type[BaseException], ...] = (SearchPermanentError,)


def is_transient(exc: BaseException) -> bool:
    return isinstance(exc, TRANSIENT_EXCEPTIONS)


def is_permanent(exc: BaseException) -> bool:
    return isinstance(exc, PERMANENT_EXCEPTIONS)
