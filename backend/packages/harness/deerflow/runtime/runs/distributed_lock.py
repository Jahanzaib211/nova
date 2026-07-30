"""Cross-replica distributed lock — closes the TOCTOU race in
``RunManager.create_or_reject()``'s "reject" strategy.

``create_or_reject()``'s own docstring already documents the guarantee it
holds locally: "This method holds the lock across both the check and the
insert, eliminating the TOCTOU race." That guarantee is real for a single
process — the lock is a plain ``asyncio.Lock``, and the inflight check
only ever looks at ``self._runs_by_thread`` (this process's own memory).
With 2+ replicas, two concurrent requests for the same thread hitting
*different* replicas each pass their own local check simultaneously,
recreating the exact race the lock was built to eliminate.

Same interface shape as ``cancel_signal.py`` (mirrors ``RunStore``'s own
abstraction pattern) — defaults to a no-op that always "succeeds",
matching today's single-replica-only behavior exactly when no Redis is
configured.
"""

from __future__ import annotations

import abc
import logging
import uuid
from typing import Any

logger = logging.getLogger(__name__)

REDIS_INSTALL = (
    "redis is required for the redis distributed lock. Install the package extra with: "
    "pip install 'deerflow-harness[redis]' (or: uv sync --all-packages --extra redis when developing locally)"
)


class DistributedLock(abc.ABC):
    """Abstract cross-process mutual-exclusion lock, keyed by an opaque string."""

    @abc.abstractmethod
    async def acquire(self, key: str, *, ttl_seconds: int) -> bool:
        """Try to acquire the lock for *key*. Returns False if already held elsewhere.

        *ttl_seconds* bounds how long the lock is held even if the holder
        crashes without releasing — a stuck lock must not wedge a thread
        forever.
        """

    @abc.abstractmethod
    async def release(self, key: str) -> None:
        """Release a lock this process holds. Safe to call even if already expired."""

    async def close(self) -> None:
        """Release backend resources. Default is a no-op."""


class NoopDistributedLock(DistributedLock):
    """Default when no Redis is configured — always "acquires" successfully.

    Matches today's single-replica behavior: the local asyncio.Lock inside
    create_or_reject() is the only mutual exclusion that exists or is needed.
    """

    async def acquire(self, key: str, *, ttl_seconds: int) -> bool:
        return True

    async def release(self, key: str) -> None:
        return None


def _ensure_redis_import():
    try:
        import redis.asyncio as redis
    except ImportError as exc:
        raise ImportError(REDIS_INSTALL) from exc
    return redis


class RedisDistributedLock(DistributedLock):
    """Redis ``SET key value NX EX ttl`` lock, released via a CAS-safe Lua
    script (only deletes if the value still matches — avoids one replica
    releasing a lock a *different* replica has since acquired after this
    one's TTL expired)."""

    _RELEASE_SCRIPT = """
    if redis.call("get", KEYS[1]) == ARGV[1] then
        return redis.call("del", KEYS[1])
    else
        return 0
    end
    """

    def __init__(self, *, redis_url: str | None = None, client: Any | None = None, key_prefix: str = "lock") -> None:
        self._key_prefix = key_prefix
        self._tokens: dict[str, str] = {}
        if client is not None:
            self._redis = client
        else:
            if not redis_url:
                raise ValueError("RedisDistributedLock requires either redis_url or client")
            redis = _ensure_redis_import()
            self._redis = redis.from_url(redis_url, decode_responses=True)

    def _full_key(self, key: str) -> str:
        return f"{self._key_prefix}:{key}"

    async def acquire(self, key: str, *, ttl_seconds: int) -> bool:
        token = uuid.uuid4().hex
        acquired = await self._redis.set(self._full_key(key), token, nx=True, ex=ttl_seconds)
        if acquired:
            self._tokens[key] = token
        return bool(acquired)

    async def release(self, key: str) -> None:
        token = self._tokens.pop(key, None)
        if token is None:
            return
        await self._redis.eval(self._RELEASE_SCRIPT, 1, self._full_key(key), token)

    async def close(self) -> None:
        await self._redis.aclose()
