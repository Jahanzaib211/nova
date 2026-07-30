"""Tests for the rate limiter backends (app/gateway/rate_limiter.py).

Both InMemoryRateLimiter and RedisRateLimiter must satisfy the same
sliding-window-log contract — parametrized so any behavioral drift between
the two backends surfaces immediately.
"""

import asyncio

import fakeredis
import pytest

from app.gateway.rate_limiter import InMemoryRateLimiter, RateLimiter, RedisRateLimiter


def _make_redis_limiter() -> RedisRateLimiter:
    return RedisRateLimiter(client=fakeredis.FakeAsyncRedis(decode_responses=True))


@pytest.fixture(params=["memory", "redis"])
def limiter(request) -> RateLimiter:
    if request.param == "memory":
        return InMemoryRateLimiter()
    return _make_redis_limiter()


@pytest.mark.anyio
async def test_allows_up_to_max_attempts(limiter: RateLimiter):
    for _ in range(5):
        result = await limiter.check("key-a", window_seconds=60, max_attempts=5)
        assert result is None


@pytest.mark.anyio
async def test_blocks_after_max_attempts(limiter: RateLimiter):
    for _ in range(5):
        await limiter.check("key-b", window_seconds=60, max_attempts=5)

    retry_after = await limiter.check("key-b", window_seconds=60, max_attempts=5)
    assert retry_after is not None
    assert retry_after > 0


@pytest.mark.anyio
async def test_blocked_hit_is_not_recorded(limiter: RateLimiter):
    """A hit that gets rejected must not itself count toward the window —
    otherwise the retry_after countdown would keep resetting forever."""
    for _ in range(3):
        await limiter.check("key-c", window_seconds=60, max_attempts=3)

    first_block = await limiter.check("key-c", window_seconds=60, max_attempts=3)
    await asyncio.sleep(0.05)
    second_block = await limiter.check("key-c", window_seconds=60, max_attempts=3)

    # retry_after should be counting down, not resetting to a fresh full window.
    assert second_block <= first_block


@pytest.mark.anyio
async def test_different_keys_are_independent(limiter: RateLimiter):
    for _ in range(5):
        await limiter.check("key-d", window_seconds=60, max_attempts=5)

    # A different key must not be affected by key-d's exhausted window.
    result = await limiter.check("key-e", window_seconds=60, max_attempts=5)
    assert result is None


@pytest.mark.anyio
async def test_window_slides_and_allows_again():
    """After the window elapses, a previously-blocked key becomes allowed again."""
    limiter = InMemoryRateLimiter()
    for _ in range(2):
        await limiter.check("key-f", window_seconds=0.1, max_attempts=2)

    blocked = await limiter.check("key-f", window_seconds=0.1, max_attempts=2)
    assert blocked is not None

    await asyncio.sleep(0.15)

    allowed = await limiter.check("key-f", window_seconds=0.1, max_attempts=2)
    assert allowed is None


@pytest.mark.anyio
async def test_redis_limiter_shared_across_instances_simulates_cross_replica():
    """The actual point of this feature: two separate RedisRateLimiter
    instances (simulating two Gateway replicas) sharing one Redis server
    must share the same quota — an attacker can't reset their limit just
    by hitting a different replica."""
    server = fakeredis.FakeServer()
    limiter_a = RedisRateLimiter(client=fakeredis.FakeAsyncRedis(server=server, decode_responses=True))
    limiter_b = RedisRateLimiter(client=fakeredis.FakeAsyncRedis(server=server, decode_responses=True))

    for _ in range(5):
        await limiter_a.check("shared-key", window_seconds=60, max_attempts=5)

    # Replica B must see the quota already exhausted by replica A.
    result = await limiter_b.check("shared-key", window_seconds=60, max_attempts=5)
    assert result is not None
