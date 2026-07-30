"""Cross-replica cancellation signal.

``RunManager.cancel()`` can only touch ``record.task``/``record.abort_event``
directly when the run is in *this* process's memory (see ``manager.py``'s
own docstring on the in-memory/store-only split). When a cancel request
lands on a *different* replica than the one running the task, the existing
store-only path already persists ``interrupted`` durably — what's missing
is a way to wake up the owning replica's run loop, which today only polls
a local ``asyncio.Event``.

This is deliberately a narrow, injectable interface (mirrors ``RunStore``'s
own abstraction) rather than ``RunManager`` reaching for a concrete Redis
client directly — keeps the harness testable without Redis, and keeps the
"no Redis configured" single-replica path behaviorally identical to before
(``NoopCancelSignal``).
"""

from __future__ import annotations

import abc
import asyncio
import logging
from typing import Any

logger = logging.getLogger(__name__)

REDIS_INSTALL = (
    "redis is required for the redis cancel signal. Install the package extra with: "
    "pip install 'deerflow-harness[redis]' (or: uv sync --all-packages --extra redis when developing locally)"
)


class CancelSignal(abc.ABC):
    """Abstract cross-process cancellation notification channel."""

    @abc.abstractmethod
    async def request_cancel(self, run_id: str) -> None:
        """Notify whichever replica owns *run_id* that cancellation was requested."""

    @abc.abstractmethod
    async def wait_for_cancel(self, run_id: str) -> None:
        """Block until a cancel request for *run_id* arrives.

        Callers run this as a background task alongside the run's main
        task, cancelling it once the run finishes (see ``services.py``'s
        ``start_run``) — it is not expected to return on its own for a run
        that completes normally.
        """

    async def close(self) -> None:
        """Release backend resources. Default is a no-op."""


class NoopCancelSignal(CancelSignal):
    """Default when no Redis is configured — single-replica behavior only.

    ``request_cancel`` is a no-op (the existing local abort_event/task.cancel
    path already handles same-process cancellation; there's no other
    replica to notify). ``wait_for_cancel`` never resolves, matching "no
    remote cancel channel exists" — the caller's own task-completion cleanup
    cancels this background wait, same as any other per-run resource.
    """

    async def request_cancel(self, run_id: str) -> None:
        return None

    async def wait_for_cancel(self, run_id: str) -> None:
        await asyncio.Event().wait()  # never set — waits until externally cancelled


def _ensure_redis_import():
    try:
        import redis.asyncio as redis
    except ImportError as exc:
        raise ImportError(REDIS_INSTALL) from exc
    return redis


class RedisCancelSignal(CancelSignal):
    """Redis pub/sub-backed cross-replica cancel signal.

    ``request_cancel`` publishes; the owning replica's ``wait_for_cancel``
    (subscribed since the run started) wakes up and sets the run's local
    ``abort_event`` — the existing streaming loop in ``worker.py`` needs no
    changes at all, it keeps polling the same local flag it always has.
    """

    def __init__(self, *, redis_url: str | None = None, client: Any | None = None, key_prefix: str = "cancel") -> None:
        self._key_prefix = key_prefix
        if client is not None:
            self._redis = client
        else:
            if not redis_url:
                raise ValueError("RedisCancelSignal requires either redis_url or client")
            redis = _ensure_redis_import()
            # socket_timeout=None: see the matching comment in
            # redis_provider.py's RedisStreamBridge — critical here
            # specifically, since wait_for_cancel()'s pubsub.listen() has
            # no bound at all (a run can legitimately run for many
            # minutes with no cancel ever arriving), unlike the stream
            # bridge's own bounded BLOCK duration.
            self._redis = redis.from_url(redis_url, decode_responses=True, socket_timeout=None)

    def _channel(self, run_id: str) -> str:
        return f"{self._key_prefix}:{run_id}"

    async def request_cancel(self, run_id: str) -> None:
        await self._redis.publish(self._channel(run_id), "1")

    async def wait_for_cancel(self, run_id: str) -> None:
        pubsub = self._redis.pubsub()
        try:
            await pubsub.subscribe(self._channel(run_id))
            async for message in pubsub.listen():
                if message.get("type") == "message":
                    return
        finally:
            await pubsub.unsubscribe(self._channel(run_id))
            await pubsub.aclose()

    async def close(self) -> None:
        await self._redis.aclose()
