"""Bridges WorkspaceEvent publications to per-connection async queues for SSE.

The WIK emits domain events through the synchronous, thread-agnostic
``EventBus`` (``deerflow.events.bus``). ``scan_async`` runs the actual scan
off the event loop via ``asyncio.to_thread``, so ``EventBus.publish`` can
fire from a worker thread while every SSE consumer lives on the request's
event loop. ``WorkspaceEventBridge`` hands each matching event to the
correct ``asyncio.Queue`` via ``call_soon_threadsafe``, so delivery is safe
regardless of which thread published the event.

Subscriptions are scoped by ``root_path`` — the same value every
``WorkspaceEvent`` already carries — rather than ``thread_id``. The SSE
endpoint resolves ``thread_id -> root_path`` once via the existing
``_workspace_root`` helper, identically to every other workspace endpoint,
so no new mapping needs to be introduced or kept in sync.
"""

from __future__ import annotations

import asyncio
import logging
import uuid
from dataclasses import dataclass

from deerflow.events.bus import EventBus
from deerflow.events.event import DomainEvent
from deerflow.workspace.events import WorkspaceEvent

logger = logging.getLogger(__name__)

DEFAULT_QUEUE_MAXSIZE = 256


@dataclass(frozen=True)
class _Subscription:
    root_path: str
    loop: asyncio.AbstractEventLoop
    queue: asyncio.Queue


class WorkspaceEventBridge:
    """Fan WorkspaceEvent publications out to per-connection SSE queues."""

    def __init__(self, bus: EventBus) -> None:
        self._bus = bus
        self._subs: dict[str, _Subscription] = {}
        # EventBus.publish special-cases only the exact ``DomainEvent``
        # class for base-class dispatch — it does not walk the full MRO.
        # Subscribing to the intermediate ``WorkspaceEvent`` class would
        # never fire for concrete subclasses like ``WorkspaceScanned``, so
        # the bridge subscribes at the ``DomainEvent`` root (the bus's one
        # true wildcard) and filters by ``isinstance`` in ``_on_event``.
        self._bus.subscribe(DomainEvent, self._on_event)

    def subscribe(self, root_path: str, *, queue_maxsize: int = DEFAULT_QUEUE_MAXSIZE) -> tuple[str, asyncio.Queue]:
        """Register a new SSE consumer for events scoped to ``root_path``.

        Must be called from within a running event loop. Returns
        ``(subscription_id, queue)``; call :meth:`unsubscribe` with the id
        once the connection closes.
        """
        sub_id = uuid.uuid4().hex
        queue: asyncio.Queue = asyncio.Queue(maxsize=queue_maxsize)
        loop = asyncio.get_running_loop()
        self._subs[sub_id] = _Subscription(root_path=root_path, loop=loop, queue=queue)
        return sub_id, queue

    def unsubscribe(self, subscription_id: str) -> None:
        self._subs.pop(subscription_id, None)

    def _on_event(self, event: DomainEvent) -> None:
        """EventBus handler — may run on a worker thread or the loop thread."""
        if not isinstance(event, WorkspaceEvent):
            return
        for sub in list(self._subs.values()):
            if event.root_path != sub.root_path:
                continue
            try:
                sub.loop.call_soon_threadsafe(self._deliver, sub.queue, event)
            except RuntimeError:
                # Target loop is closed (client already disconnected and
                # its generator was torn down) — nothing to deliver to.
                logger.debug("Dropped %s: subscriber loop is closed", type(event).__name__)

    @staticmethod
    def _deliver(queue: asyncio.Queue, event: WorkspaceEvent) -> None:
        try:
            queue.put_nowait(event)
        except asyncio.QueueFull:
            # Slow consumer: drop the oldest event to make room rather than
            # growing unbounded or blocking the publisher.
            try:
                queue.get_nowait()
            except asyncio.QueueEmpty:
                pass
            try:
                queue.put_nowait(event)
            except asyncio.QueueFull:
                pass


_bridge: WorkspaceEventBridge | None = None


def get_workspace_event_bridge() -> WorkspaceEventBridge:
    """Return the process-wide bridge, bound to the global event_bus."""
    global _bridge
    if _bridge is None:
        from deerflow.events.bus import event_bus

        _bridge = WorkspaceEventBridge(event_bus)
    return _bridge
