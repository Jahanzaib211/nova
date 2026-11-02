"""Abstract stream bridge protocol.

StreamBridge decouples agent workers (producers) from SSE endpoints
(consumers), aligning with LangGraph Platform's Queue + StreamManager
architecture.
"""

from __future__ import annotations

import abc
from collections.abc import AsyncIterator
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class StreamEvent:
    """Single stream event.

    Attributes:
        id: Monotonically increasing event ID (used as SSE ``id:`` field,
            supports ``Last-Event-ID`` reconnection).
        event: SSE event name, e.g. ``"metadata"``, ``"updates"``,
            ``"events"``, ``"error"``, ``"end"``.
        data: JSON-serialisable payload.
    """

    id: str
    event: str
    data: Any


HEARTBEAT_SENTINEL = StreamEvent(id="", event="__heartbeat__", data=None)
END_SENTINEL = StreamEvent(id="", event="__end__", data=None)


class StreamBridge(abc.ABC):
    """Abstract base for stream bridges."""

    @abc.abstractmethod
    async def publish(self, run_id: str, event: str, data: Any) -> None:
        """Enqueue a single event for *run_id* (producer side)."""

    @abc.abstractmethod
    async def publish_end(self, run_id: str) -> None:
        """Signal that no more events will be produced for *run_id*."""

    @abc.abstractmethod
    def subscribe(
        self,
        run_id: str,
        *,
        last_event_id: str | None = None,
        heartbeat_interval: float = 15.0,
    ) -> AsyncIterator[StreamEvent]:
        """Async iterator that yields events for *run_id* (consumer side).

        Yields :data:`HEARTBEAT_SENTINEL` when no event arrives within
        *heartbeat_interval* seconds.  Yields :data:`END_SENTINEL` once
        the producer calls :meth:`publish_end`.
        """

    async def has_run(self, run_id: str) -> bool:
        """Whether events for *run_id* are currently retained.

        Consumers use this to avoid subscribing to a run whose buffer was
        already released (``cleanup``): ``subscribe`` on such a run would
        lazily create a fresh, never-ending stream and heartbeat forever.
        Defaults to ``True`` so implementations without cheap retention
        checks keep today's behaviour.

        Async because a network-backed implementation (e.g. Redis) cannot
        answer this without an I/O call — a synchronous method here would
        either block the event loop or force a fake synchronous wrapper
        around an inherently async client. In-memory implementations that
        answer from a local dict pay no real cost for the ``await``.
        """
        return True

    @abc.abstractmethod
    async def cleanup(self, run_id: str, *, delay: float = 0) -> None:
        """Release resources associated with *run_id*.

        If *delay* > 0 the implementation should wait before releasing,
        giving late subscribers a chance to drain remaining events.
        """

    async def close(self) -> None:
        """Release backend resources.  Default is a no-op."""
