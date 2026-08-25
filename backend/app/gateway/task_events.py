"""Thread-scoped task-event hub and its WebSocket transport (WS-G).

Subagent lifecycle events are produced inside the harness as ``custom``
stream events on a per-RUN bridge. The panel needs them scoped to the
THREAD — runs are transient, reconnects happen, and todo bindings must
survive both. This module mirrors those events into a thread-keyed hub and
serves it over one WebSocket.

The mirror wraps whatever ``StreamBridge`` the gateway runs (memory or
Redis) as a decorator, so no harness code changes and non-task traffic pays
one isinstance check.
"""

from __future__ import annotations

import asyncio
import json
import logging
from collections import deque
from collections.abc import AsyncIterator, Awaitable, Callable
from typing import Any

from deerflow.runtime.stream_bridge.base import StreamBridge

logger = logging.getLogger(__name__)


class TaskEventHub:
    """Per-thread ring buffer plus live subscriber queues.

    - Late joiners replay the buffer, so a panel opened mid-run still learns
      which todos already settled.
    - A slow consumer's queue drops OLDEST entries rather than blocking the
      run path or tearing down the socket; live tail beats full history here.
    """

    def __init__(self, *, buffer_size: int = 64, subscriber_queue_size: int = 256) -> None:
        self._buffer_size = buffer_size
        self._queue_size = subscriber_queue_size
        # Plain dicts, NOT defaultdict: a publish for an unknown thread must
        # not materialise empty bookkeeping entries that then live forever.
        self._buffers: dict[str, deque[dict]] = {}
        self._subscribers: dict[str, set[asyncio.Queue]] = {}

    async def publish(self, thread_id: str, payload: dict) -> None:
        buffer = self._buffers.setdefault(
            thread_id, deque(maxlen=self._buffer_size)
        )
        buffer.append(payload)
        subscribers = self._subscribers.get(thread_id)
        if not subscribers:
            # Nobody listening: trim the buffers dict back once threads go
            # quiet, so long uptimes don't accumulate one entry per thread.
            if not buffer and thread_id in self._buffers:
                del self._buffers[thread_id]
            return
        dead: list[asyncio.Queue] = []
        for queue in subscribers:
            try:
                queue.put_nowait(payload)
            except asyncio.QueueFull:
                try:
                    queue.get_nowait()  # drop oldest
                    queue.put_nowait(payload)
                except (asyncio.QueueFull, asyncio.QueueEmpty):
                    dead.append(queue)
        for queue in dead:
            subscribers.discard(queue)
            logger.warning("TaskEventHub: dropped unresponsive task-ws subscriber")

    def subscribe(self, thread_id: str) -> AsyncIterator[dict]:
        return self._subscribe(thread_id)

    async def _subscribe(self, thread_id: str) -> AsyncIterator[dict]:
        queue: asyncio.Queue = asyncio.Queue(maxsize=self._queue_size)
        # Snapshot-then-register so nothing published between replay and
        # registration is lost or duplicated.
        replay = list(self._buffers.get(thread_id, ()))
        self._subscribers.setdefault(thread_id, set()).add(queue)
        try:
            for payload in replay:
                yield payload
            while True:
                payload = await queue.get()
                yield payload
        finally:
            subscribers = self._subscribers.get(thread_id)
            if subscribers is not None:
                subscribers.discard(queue)
                if not subscribers:
                    self._subscribers.pop(thread_id, None)


def _is_task_custom_event(event: str, data: Any) -> bool:
    return (
        event == "custom"
        and isinstance(data, dict)
        and str(data.get("type", "")).startswith("task_")
    )


class MirroringStreamBridge(StreamBridge):
    """Decorator over any StreamBridge that duplicates task_* custom events
    into a TaskEventHub.

    Everything else — publish payloads, subscribe iterators, end sentinels,
    heartbeats — delegates untouched, so this is safe in front of both the
    in-memory and Redis bridges.
    """

    def __init__(
        self,
        *,
        inner: StreamBridge,
        hub: TaskEventHub,
        resolve_run_thread: Callable[[str], Awaitable[str | None]],
    ) -> None:
        self._inner = inner
        self._hub = hub
        self._resolve_run_thread = resolve_run_thread

    async def _mirror(self, data: Any, run_id: str) -> None:
        if not _is_task_custom_event("custom", data):
            return
        try:
            thread_id = await self._resolve_run_thread(run_id)
            if thread_id:
                await self._hub.publish(thread_id, data)
        except Exception:  # noqa: BLE001 - mirroring must never break the run stream
            logger.warning("task-event mirror failed for run %s", run_id, exc_info=True)

    async def publish(self, run_id: str, event: str, data: Any) -> None:
        if event == "custom":
            await self._mirror(data, run_id)
        await self._inner.publish(run_id, event, data)

    async def publish_end(self, run_id: str) -> None:
        await self._inner.publish_end(run_id)

    async def has_run(self, run_id: str) -> bool:
        return await self._inner.has_run(run_id)

    async def cleanup(self, run_id: str, *, delay: float = 0) -> None:
        await self._inner.cleanup(run_id, delay=delay)

    async def close(self) -> None:
        await self._inner.close()

    def subscribe(
        self,
        run_id: str,
        *,
        last_event_id: str | None = None,
        heartbeat_interval: float = 15.0,
    ) -> AsyncIterator[StreamEvent]:  # noqa: F821 - imported under TYPE_CHECKING in base
        return self._inner.subscribe(
            run_id,
            last_event_id=last_event_id,
            heartbeat_interval=heartbeat_interval,
        )


def encode_task_ws_message(payload: dict) -> str:
    return json.dumps(payload)


async def run_tasks_ws_stream(websocket: Any, hub: TaskEventHub, thread_id: str) -> None:
    """Accept an already-authenticated websocket and stream hub events.

    Split from the FastAPI handler so admission checks stay testable without
    a socket server.
    """
    from starlette.websockets import WebSocketState

    await websocket.accept()
    gen = hub.subscribe(thread_id)
    try:
        async for payload in gen:
            if websocket.client_state != WebSocketState.CONNECTED:
                break
            await websocket.send_text(encode_task_ws_message(payload))
    except Exception as exc:  # noqa: BLE001 - client vanished mid-send
        logger.debug("tasks-ws %s closed: %s", thread_id, exc)
    finally:
        close = getattr(gen, "aclose", None)
        if close is not None:
            await close()
        if websocket.client_state == WebSocketState.CONNECTED:
            await websocket.close()
