"""Redis Streams-backed stream bridge — the cross-replica implementation.

Makes multiple Gateway replicas share stream state: a run created on one
replica can be joined/streamed from another, since the event log lives in
Redis instead of a process-local dict. See ``k8s/ARCHITECTURE.md`` §4/§10
for why this was the actual blocker to horizontal Gateway scaling.

Design note: ``MemoryStreamBridge.subscribe()`` is already an offset/cursor
replay loop, and its ``StreamEvent.id`` format (``"{ts_ms}-{seq}"``) is
coincidentally shaped like a Redis Stream ID (``ms-seq``) already — this
implementation leans into that by using Redis's own auto-generated stream
IDs directly as ``StreamEvent.id``, rather than maintaining a parallel
counter. ``XREAD`` with a specific ID as the cursor already returns only
entries with a strictly greater ID, which is exactly "resume after
last_event_id" — no offset math needs reimplementing.
"""

from __future__ import annotations

import json
import logging
from collections.abc import AsyncIterator
from typing import Any

from .base import END_SENTINEL, HEARTBEAT_SENTINEL, StreamBridge, StreamEvent

logger = logging.getLogger(__name__)

REDIS_INSTALL = "redis is required for the redis stream bridge. Install the package extra with: pip install 'deerflow-harness[redis]' (or: uv sync --all-packages --extra redis when developing locally)"

_END_EVENT_NAME = "__end__"
_READ_COUNT = 100


def _ensure_redis_import():
    """Import and return the redis.asyncio module, raising ImportError on failure."""
    try:
        import redis.asyncio as redis
    except ImportError as exc:
        raise ImportError(REDIS_INSTALL) from exc
    return redis


class RedisStreamBridge(StreamBridge):
    """Stream bridge backed by Redis Streams (``XADD``/``XREAD BLOCK``).

    Cross-process by construction — any Gateway replica holding a
    connection to the same Redis instance can publish to and subscribe
    from the same run's stream.
    """

    def __init__(
        self,
        *,
        redis_url: str | None = None,
        client: Any | None = None,
        queue_maxsize: int = 256,
        key_prefix: str = "streambridge",
    ) -> None:
        """*client* is a test-only seam (a fakeredis instance) — production
        callers pass *redis_url* and get a real ``redis.asyncio.Redis``."""
        self._maxsize = queue_maxsize
        self._key_prefix = key_prefix
        if client is not None:
            self._redis = client
        else:
            if not redis_url:
                raise ValueError("RedisStreamBridge requires either redis_url or client")
            redis = _ensure_redis_import()
            # Caught live: redis-py's client-side socket_timeout applies to
            # EVERY command, including XREAD BLOCK — a genuinely-blocking
            # read that legitimately takes the full `block` duration raises
            # a spurious client-side TimeoutError once it exceeds
            # socket_timeout, well before the server itself would time out.
            # socket_timeout=None disables the client-side timeout entirely,
            # which is correct here: `subscribe()`'s own `block=heartbeat_interval*1000`
            # argument is already the real bound on how long a single read
            # waits, and connection health is covered separately by the
            # readiness/liveness probes on the Redis Deployment itself.
            self._redis = redis.from_url(redis_url, decode_responses=True, socket_timeout=None)

    def _key(self, run_id: str) -> str:
        return f"{self._key_prefix}:{run_id}"

    # -- StreamBridge API ------------------------------------------------------

    async def has_run(self, run_id: str) -> bool:
        return bool(await self._redis.exists(self._key(run_id)))

    async def publish(self, run_id: str, event: str, data: Any) -> None:
        await self._redis.xadd(
            self._key(run_id),
            {"event": event, "data": json.dumps(data)},
            maxlen=self._maxsize,
            approximate=True,
        )

    async def publish_end(self, run_id: str) -> None:
        # A sentinel entry in the same stream (not a companion key) so
        # ordering is preserved for any subscriber reading forward,
        # including one that attaches after the producer already finished.
        await self._redis.xadd(
            self._key(run_id),
            {"event": _END_EVENT_NAME, "data": "null"},
            maxlen=self._maxsize,
            approximate=True,
        )

    async def subscribe(
        self,
        run_id: str,
        *,
        last_event_id: str | None = None,
        heartbeat_interval: float = 15.0,
    ) -> AsyncIterator[StreamEvent]:
        key = self._key(run_id)
        # "0" replays from the earliest retained entry (matches
        # MemoryStreamBridge's behaviour when no last_event_id is given —
        # it resumes from the start of the *retained* buffer, not just
        # future events). A specific last_event_id relies on XREAD's own
        # semantics: it returns only entries with ID strictly greater than
        # the one given, which is exactly "resume after".
        cursor = last_event_id if last_event_id is not None else "0"
        block_ms = int(heartbeat_interval * 1000)

        while True:
            result = await self._redis.xread({key: cursor}, count=_READ_COUNT, block=block_ms)
            if not result:
                yield HEARTBEAT_SENTINEL
                continue

            # result shape: [(stream_key, [(entry_id, {field: value, ...}), ...])]
            _, entries = result[0]
            for entry_id, fields in entries:
                cursor = entry_id
                if fields.get("event") == _END_EVENT_NAME:
                    yield END_SENTINEL
                    return
                yield StreamEvent(id=entry_id, event=fields.get("event", ""), data=json.loads(fields.get("data", "null")))

    async def cleanup(self, run_id: str, *, delay: float = 0) -> None:
        key = self._key(run_id)
        if delay > 0:
            # TTL, not an in-process asyncio.sleep + delete: the process
            # that finished the run and the process that eventually
            # cleans it up may not be the same replica, and a TTL survives
            # this process dying mid-wait in a way a local sleep can't.
            await self._redis.expire(key, int(delay))
        else:
            await self._redis.delete(key)

    async def close(self) -> None:
        await self._redis.aclose()
