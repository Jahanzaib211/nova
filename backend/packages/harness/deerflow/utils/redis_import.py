"""Lazy import of the optional ``redis`` extra.

Four Redis-backed components (stream bridge, cancel signal, distributed
lock, rate limiter) each carried a copy of the same import guard; only the
component named in the install hint differed.
"""

from __future__ import annotations

from typing import Any


def redis_install_hint(component: str) -> str:
    return f"redis is required for the redis {component}. Install the package extra with: pip install 'deerflow-harness[redis]' (or: uv sync --all-packages --extra redis when developing locally)"


def import_redis_asyncio(component: str) -> Any:
    """Return ``redis.asyncio``; ``ImportError`` naming ``component`` and the extra when absent."""
    try:
        import redis.asyncio as redis
    except ImportError as exc:
        raise ImportError(redis_install_hint(component)) from exc
    return redis
