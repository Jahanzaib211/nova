"""Thread-scoped task-event hub and its WebSocket transport (WS-G).

Subagent lifecycle events are produced inside the harness as ``custom``
stream events on a per-RUN bridge. The panel needs them scoped to the
THREAD — runs are transient, reconnects happen, and todo bindings must
survive both. This module mirrors those events into thread-keyed hubs and
serves them over WebSockets:

- ``/api/threads/{id}/tasks-ws``    — task channel only (SSE parity)
- ``/api/threads/{id}/computer-ws`` — multiplexed: tasks + browser
  (dev-server transitions) + workspace (observation facts)

The mirror wraps whatever ``StreamBridge`` the gateway runs (memory or
Redis) as a decorator, so no harness code changes and non-task traffic pays
one isinstance check.

**Single-replica caveat (deliberate).** Hubs are in-process. On one box —
this deployment's shape — that is exactly right. A multi-replica gateway
must fan emitters out via Redis pub/sub, or a socket attached to replica B
never sees replica A's mirrors. Documented here so scaling does not
silently drop the feeds.
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

# Server-side heartbeat: an idle task socket is NORMAL (subagents take
# minutes), but idle connections die silently on NATs and middleboxes. A
# tiny JSON ping keeps them alive; clients ignore unknown frames by design.
HEARTBEAT_SECONDS = 30.0


class TaskEventHub:
    """Per-thread ring buffer plus live subscriber queues.

    Generic across channels (tasks, browser, workspace observations) — the
    channel discriminator rides inside each payload. Used by both the
    ``tasks-ws`` and ``computer-ws`` sockets.

    - Late joiners replay the buffer, so a panel opened mid-run still learns
      which todos already settled.
    - A slow consumer's queue drops OLDEST entries rather than blocking the
      run path or tearing down the socket; live tail beats full history here.
    """

    def __init__(self, *, buffer_size: int = 64, subscriber_queue_size: int = 256) -> None:
        self._buffer_size = buffer_size
        self._queue_size = subscriber_queue_size
        self._loop: asyncio.AbstractEventLoop | None = None
        # Plain dicts, NOT defaultdict: a publish for an unknown thread must
        # not materialise empty bookkeeping entries that then live forever.
        self._buffers: dict[str, deque[dict]] = {}
        self._subscribers: dict[str, set[asyncio.Queue]] = {}

    def attach_loop(self, loop: asyncio.AbstractEventLoop) -> None:
        """Bind the lifespan loop so cross-thread emitters can fan in safely."""
        self._loop = loop

    def publish_threadsafe(self, thread_id: str, payload: dict) -> None:
        """Publish from ANY thread (watchdog threads included).

        Marshals onto the bound loop; if the loop is gone we are shutting
        down — drop silently.
        """
        loop = self._loop
        if loop is None or loop.is_closed():
            return

        def _on_loop() -> None:
            try:
                asyncio.get_running_loop().create_task(self.publish(thread_id, payload))
            except RuntimeError:
                pass

        loop.call_soon_threadsafe(_on_loop)

    async def publish(self, thread_id: str, payload: dict) -> None:
        buffer = self._buffers.setdefault(thread_id, deque(maxlen=self._buffer_size))
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
    return event == "custom" and isinstance(data, dict) and str(data.get("type", "")).startswith("task_")


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


async def merged_stream(hub: TaskEventHub, thread_id: str) -> AsyncIterator[dict]:
    """Replay-then-live stream for the multiplexed computer socket."""
    return hub.subscribe(thread_id)


async def run_tasks_ws_stream(websocket: Any, hub: TaskEventHub, thread_id: str) -> None:
    """Accept an already-authenticated websocket and stream hub events.

    Split from the FastAPI handler so admission checks stay testable without
    a socket server.
    """
    from starlette.websockets import WebSocketState

    await websocket.accept()
    gen = hub.subscribe(thread_id)
    try:
        while True:
            if websocket.client_state != WebSocketState.CONNECTED:
                break
            try:
                payload = await asyncio.wait_for(gen.__anext__(), timeout=HEARTBEAT_SECONDS)
            except TimeoutError:
                await websocket.send_text('{"type":"ping"}')
                continue
            except StopAsyncIteration:
                break
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


async def run_computer_ws_stream(
    websocket: Any,
    hubs: list[TaskEventHub],
    thread_id: str,
) -> None:
    """Multiplex several hubs onto one socket (the computer panel's feed).

    Each hub replays its own buffer on join, then fans live events into a
    single outbound queue. Payloads carry their channel discriminator inside
    (``channel: "browser" | "workspace"`` for the newer feeds; task events
    keep their bare ``task_*`` shape for the legacy SSE-parity path).
    """
    from starlette.websockets import WebSocketState

    await websocket.accept()
    out: asyncio.Queue = asyncio.Queue()

    async def _pump(hub: TaskEventHub) -> None:
        try:
            async for payload in hub.subscribe(thread_id):
                await out.put(payload)
        except Exception:  # noqa: BLE001 - one stalled channel must not kill the rest
            logger.debug("computer-ws channel pump ended for %s", thread_id)

    pumps = [asyncio.create_task(_pump(hub)) for hub in hubs]
    try:
        while True:
            if websocket.client_state != WebSocketState.CONNECTED:
                break
            try:
                payload = await asyncio.wait_for(out.get(), timeout=HEARTBEAT_SECONDS)
            except TimeoutError:
                await websocket.send_text('{"type":"ping"}')
                continue
            if websocket.client_state != WebSocketState.CONNECTED:
                break
            await websocket.send_text(encode_task_ws_message(payload))
    except Exception as exc:  # noqa: BLE001 - client vanished mid-send
        logger.debug("computer-ws %s closed: %s", thread_id, exc)
    finally:
        for task in pumps:
            task.cancel()
        if websocket.client_state == WebSocketState.CONNECTED:
            await websocket.close()
