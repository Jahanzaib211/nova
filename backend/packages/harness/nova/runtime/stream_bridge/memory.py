"""In-memory stream bridge backed by an in-process event log."""

from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from typing import Any

from .base import END_SENTINEL, HEARTBEAT_SENTINEL, StreamBridge, StreamEvent
from .diagnostics import (
    record_bridge_publish,
    record_bridge_subscribe_close,
    record_bridge_subscribe_resolve,
    record_bridge_subscribe_yield,
)

logger = logging.getLogger(__name__)


@dataclass
class _RunStream:
    events: list[StreamEvent] = field(default_factory=list)
    condition: asyncio.Condition = field(default_factory=asyncio.Condition)
    ended: bool = False
    start_offset: int = 0


class MemoryStreamBridge(StreamBridge):
    """Per-run in-memory event log implementation.

    Events are retained for a bounded time window per run so late subscribers
    and reconnecting clients can replay buffered events from ``Last-Event-ID``.
    """

    def __init__(self, *, queue_maxsize: int = 256) -> None:
        self._maxsize = queue_maxsize
        self._streams: dict[str, _RunStream] = {}
        self._counters: dict[str, int] = {}

    # -- helpers ---------------------------------------------------------------

    def _get_or_create_stream(self, run_id: str) -> _RunStream:
        if run_id not in self._streams:
            self._streams[run_id] = _RunStream()
            self._counters[run_id] = 0
        return self._streams[run_id]

    def _next_id(self, run_id: str) -> str:
        self._counters[run_id] = self._counters.get(run_id, 0) + 1
        ts = int(time.time() * 1000)
        seq = self._counters[run_id] - 1
        return f"{ts}-{seq}"

    def _resolve_start_offset(self, stream: _RunStream, last_event_id: str | None) -> int:
        if last_event_id is None:
            return stream.start_offset

        for index, entry in enumerate(stream.events):
            if entry.id == last_event_id:
                return stream.start_offset + index + 1

        if stream.events:
            logger.warning(
                "last_event_id=%s not found in retained buffer; replaying from earliest retained event",
                last_event_id,
            )
        return stream.start_offset

    # -- StreamBridge API ------------------------------------------------------

    async def has_run(self, run_id: str) -> bool:
        return run_id in self._streams

    async def publish(self, run_id: str, event: str, data: Any) -> None:
        stream = self._get_or_create_stream(run_id)
        entry = StreamEvent(id=self._next_id(run_id), event=event, data=data)
        dropped = 0
        async with stream.condition:
            stream.events.append(entry)
            if len(stream.events) > self._maxsize:
                overflow = len(stream.events) - self._maxsize
                del stream.events[:overflow]
                stream.start_offset += overflow
                dropped = overflow
            stream.condition.notify_all()
        record_bridge_publish(
            run_id=run_id,
            event_id=entry.id,
            event=event,
            buffer_size=len(stream.events),
            start_offset=stream.start_offset,
            dropped_count=dropped,
        )

    async def publish_end(self, run_id: str) -> None:
        stream = self._get_or_create_stream(run_id)
        async with stream.condition:
            stream.ended = True
            stream.condition.notify_all()
        record_bridge_publish(
            run_id=run_id,
            event_id="",
            event="<end>",
            buffer_size=len(stream.events),
            start_offset=stream.start_offset,
            dropped_count=0,
        )

    async def subscribe(
        self,
        run_id: str,
        *,
        last_event_id: str | None = None,
        heartbeat_interval: float = 15.0,
    ) -> AsyncIterator[StreamEvent]:
        stream = self._get_or_create_stream(run_id)
        async with stream.condition:
            next_offset = self._resolve_start_offset(stream, last_event_id)
        record_bridge_subscribe_resolve(
            run_id=run_id,
            last_event_id=last_event_id,
            resolved_offset=next_offset,
            start_offset=stream.start_offset,
            retained_events=len(stream.events),
        )

        close_reason: str = "unknown"
        try:
            while True:
                async with stream.condition:
                    if next_offset < stream.start_offset:
                        logger.warning(
                            "subscriber for run %s fell behind retained buffer; resuming from offset %s",
                            run_id,
                            stream.start_offset,
                        )
                        next_offset = stream.start_offset

                    local_index = next_offset - stream.start_offset
                    if 0 <= local_index < len(stream.events):
                        entry = stream.events[local_index]
                        next_offset += 1
                    elif stream.ended:
                        entry = END_SENTINEL
                    else:
                        try:
                            await asyncio.wait_for(stream.condition.wait(), timeout=heartbeat_interval)
                        except TimeoutError:
                            entry = HEARTBEAT_SENTINEL
                        else:
                            continue

                if entry is END_SENTINEL:
                    record_bridge_subscribe_yield(run_id=run_id, kind="end", event_id=getattr(entry, "id", None))
                    # Set the close reason BEFORE yielding so the finally
                    # block sees the right value when the consumer breaks
                    # the loop and Python raises GeneratorExit on us.
                    close_reason = "end_sentinel"
                    yield END_SENTINEL
                    return
                if entry is HEARTBEAT_SENTINEL:
                    record_bridge_subscribe_yield(run_id=run_id, kind="heartbeat")
                    yield HEARTBEAT_SENTINEL
                    continue
                record_bridge_subscribe_yield(
                    run_id=run_id,
                    kind="event",
                    event_id=entry.id,
                )
                yield entry
        except asyncio.CancelledError:
            close_reason = "cancelled"
            raise
        except GeneratorExit:
            # The consumer broke the loop (or our task was cancelled).
            # ``close_reason`` already reflects the most recent intent:
            # "end_sentinel" if we just yielded END_SENTINEL, otherwise
            # the default "unknown". Promote unknown to generator_exit so
            # the recorded reason always tells the operator something
            # meaningful.
            if close_reason == "unknown":
                close_reason = "generator_exit"
            raise
        except Exception as exc:  # noqa: BLE001 — observability boundary
            close_reason = f"exception:{type(exc).__name__}"
            raise
        finally:
            record_bridge_subscribe_close(run_id=run_id, thread_id=None, reason=close_reason)

    async def cleanup(self, run_id: str, *, delay: float = 0) -> None:
        if delay > 0:
            await asyncio.sleep(delay)
        self._streams.pop(run_id, None)
        self._counters.pop(run_id, None)

    async def close(self) -> None:
        self._streams.clear()
        self._counters.clear()
