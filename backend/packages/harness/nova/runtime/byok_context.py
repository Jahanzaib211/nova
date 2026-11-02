"""Task-local bring-your-own-key (BYOK) API key override.

Mirrors the ``user_context`` pattern: a task-local ``ContextVar`` that the
gateway sets at run start (writer side, in ``app.gateway``) and the model
factory reads (reader side, in ``deerflow.models``) to override the API key
for a run. Keeping the contextvar in the harness layer preserves the
harness→app import boundary — the factory never imports gateway code.

Asyncio semantics match ``user_context``: ``ContextVar`` is task-local and
``asyncio.create_task`` copies the current context, so a key set before the
run task is created is captured into that task.
"""

from __future__ import annotations

from contextvars import ContextVar, Token

_current_byok_api_key: ContextVar[str | None] = ContextVar("deerflow_byok_api_key", default=None)


def set_byok_api_key(key: str | None) -> Token[str | None]:
    """Set the BYOK API key override for this async task."""
    return _current_byok_api_key.set(key)


def get_byok_api_key() -> str | None:
    """Return the BYOK API key override for this task, or None."""
    return _current_byok_api_key.get()


def reset_byok_api_key(token: Token[str | None]) -> None:
    """Restore the previous BYOK context using the token from ``set``."""
    _current_byok_api_key.reset(token)
