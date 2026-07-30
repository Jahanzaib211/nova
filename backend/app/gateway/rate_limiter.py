"""Rate limiter backends for ``auth_rate_limit_middleware.py``.

Extracted the sliding-window-log algorithm out of the middleware itself so
it can be backed by either an in-process deque (single replica) or Redis
(multi-replica) behind the same interface — the middleware doesn't need to
know which.

Both implementations use the exact same sliding-window-log semantics (not
a cheaper fixed-window counter): a fixed window would let a client burst
up to 2x max_attempts across a window boundary, which matters here since
one of the two tiers this guards is brute-force login protection.
"""

from __future__ import annotations

import abc
import logging
import time
import uuid
from collections import defaultdict, deque
from typing import Any

logger = logging.getLogger(__name__)

REDIS_INSTALL = (
    "redis is required for the redis rate limiter. Install the package extra with: "
    "pip install 'deerflow-harness[redis]' (or: uv sync --all-packages --extra redis when developing locally)"
)


class RateLimiter(abc.ABC):
    """Sliding-window-log rate limiter, keyed by an opaque string."""

    @abc.abstractmethod
    async def check(self, key: str, *, window_seconds: float, max_attempts: int) -> int | None:
        """Record a hit for *key*; return Retry-After seconds if over the
        limit (the hit is NOT recorded in that case), else None."""

    async def close(self) -> None:
        """Release backend resources. Default is a no-op."""


class InMemoryRateLimiter(RateLimiter):
    """Single-process sliding-window log — the original algorithm from
    AuthRateLimitMiddleware, unchanged, just extracted behind this interface."""

    def __init__(self) -> None:
        self._hits: dict[str, deque[float]] = defaultdict(deque)

    async def check(self, key: str, *, window_seconds: float, max_attempts: int) -> int | None:
        now = time.monotonic()
        window = self._hits[key]
        cutoff = now - window_seconds
        while window and window[0] < cutoff:
            window.popleft()

        if len(window) >= max_attempts:
            return int(window_seconds - (now - window[0])) + 1

        window.append(now)

        # Opportunistic cleanup so the dict doesn't grow unbounded.
        if len(self._hits) > 10_000:
            global_cutoff = now - window_seconds
            for k in [k for k, v in self._hits.items() if not v or v[-1] < global_cutoff]:
                self._hits.pop(k, None)

        return None


def _ensure_redis_import():
    try:
        import redis.asyncio as redis
    except ImportError as exc:
        raise ImportError(REDIS_INSTALL) from exc
    return redis


class RedisRateLimiter(RateLimiter):
    """Redis sorted-set sliding-window log.

    Deliberately avoids Lua scripting (``EVAL``/``EVALSHA``) — some managed
    Redis-compatible services restrict it, and it's untestable against
    fakeredis (confirmed: no EVAL support at all), which would mean this
    class could never actually be exercised by this repo's test suite.

    Trade-off, stated plainly: this is "add optimistically, then check and
    undo if over" across 4 separate round trips, not one atomic operation.
    Under extreme concurrent bursts on the *same* key, a small number of
    requests beyond max_attempts can slip through in the race window
    between two replicas' ZCARD reads. This is an intentionally different
    risk posture than cancel_signal.py/distributed_lock.py's Lua-scripted
    exactness — those guard correctness invariants (double-cancel,
    duplicate run creation); this guards abuse-rate throttling, where
    being off by a couple of requests during a burst is a normal,
    accepted trade-off for rate limiters in general (matches e.g. the
    common "fixed window counter" approach's own well-known boundary
    slop, just smaller).

    Uses ``time.time()`` (wall clock), not ``time.monotonic()`` — monotonic
    clock values have an arbitrary per-process epoch and are meaningless
    compared across processes/machines, which matters the moment this is
    shared by more than one replica.
    """

    def __init__(self, *, redis_url: str | None = None, client: Any | None = None, key_prefix: str = "ratelimit") -> None:
        self._key_prefix = key_prefix
        if client is not None:
            self._redis = client
        else:
            if not redis_url:
                raise ValueError("RedisRateLimiter requires either redis_url or client")
            redis = _ensure_redis_import()
            self._redis = redis.from_url(redis_url, decode_responses=True)

    async def check(self, key: str, *, window_seconds: float, max_attempts: int) -> int | None:
        now = time.time()
        cutoff = now - window_seconds
        full_key = f"{self._key_prefix}:{key}"
        member = f"{now}-{uuid.uuid4().hex}"  # unique even for same-timestamp concurrent hits

        await self._redis.zadd(full_key, {member: now})
        await self._redis.zremrangebyscore(full_key, "-inf", cutoff)
        count = await self._redis.zcard(full_key)

        if count > max_attempts:
            await self._redis.zrem(full_key, member)  # undo our own optimistic add
            oldest = await self._redis.zrange(full_key, 0, 0, withscores=True)
            oldest_score = oldest[0][1] if oldest else now
            return int(window_seconds - (now - oldest_score)) + 1

        await self._redis.expire(full_key, int(window_seconds) + 1)
        return None

    async def close(self) -> None:
        await self._redis.aclose()
