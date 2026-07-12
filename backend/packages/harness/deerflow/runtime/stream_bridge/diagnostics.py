"""Pipeline diagnostics for the SSE streaming path.

This module is **observability-only**. It MUST NOT change any production
behavior. All entry points are no-ops when the ``DEER_FLOW_STREAM_TRACE``
environment variable is not set to ``"1"``.

The goal is to instrument every boundary of the streaming pipeline
without coupling the instrumented code to a specific destination. Each
recording carries enough information to reconstruct the per-event timeline
needed for the long-session frontend-freeze investigation.

Boundaries instrumented:

1. ``record_worker_publish`` — worker → bridge publish (runtime/runs/worker.py)
2. ``record_bridge_publish`` — bridge internal publish (stream_bridge/memory.py)
3. ``record_bridge_subscribe_resolve`` — bridge subscribe last_event_id resolution
4. ``record_bridge_subscribe_yield`` — bridge subscribe yielded entry (heartbeat / event / end)
5. ``record_sse_format`` — sse_consumer format_sse byte count
6. ``record_sse_consumer_loop_iter`` — sse_consumer per-iteration boundary
7. ``record_sse_consumer_disconnect`` — sse_consumer detected request.is_disconnected()

For every record the diagnostic captures:

* monotonic_ns — high-resolution monotonic timestamp (for inter-event latency)
* wall_iso — ISO-8601 wall clock (for cross-process correlation with logs)
* run_id — the LangGraph run UUID (sanitised to remove newlines)
* thread_id — the thread UUID (when available; sanitised)
* event_id — the SSE event id (when yielded from the bridge)
* seq — monotonic sequence number assigned at record creation
* stage — short string identifying which boundary fired
* extra — JSON-safe dict of additional context

Recording is best-effort: any error inside the recorder is swallowed
and logged at DEBUG. The recorder itself never raises.

The recorder supports two sinks:

* stdout (default) — line-delimited JSON written to a file when
  ``DEER_FLOW_STREAM_TRACE_FILE`` is set, else to stderr. The format is
  machine-parseable so it can be sliced by jq without a schema doc.
* in-memory ring buffer — ``read_recent`` returns the last N records so
  a debugging endpoint can dump them. Default ring size: 10_000.

The recorder is process-singleton. All records share one sequence number
counter so cross-boundary ordering can be reconstructed without relying
on wall-clock skew.
"""

from __future__ import annotations

import json
import logging
import os
import sys
import threading
import time
from collections import deque
from typing import Any, Deque

logger = logging.getLogger(__name__)


def _sanitize(value: str | None) -> str | None:
    if value is None:
        return None
    return str(value).replace("\n", "").replace("\r", "")


class _Diagnostics:
    """Process-singleton recorder for streaming pipeline events.

    Activated by ``DEER_FLOW_STREAM_TRACE=1``. Disabled by default.
    """

    def __init__(self) -> None:
        self._enabled = os.environ.get("DEER_FLOW_STREAM_TRACE") == "1"
        self._file_path = os.environ.get("DEER_FLOW_STREAM_TRACE_FILE") or None
        self._ring_size = int(os.environ.get("DEER_FLOW_STREAM_TRACE_RING", "10000"))
        self._ring: Deque[dict[str, Any]] = deque(maxlen=self._ring_size)
        self._lock = threading.Lock()
        self._seq = 0
        self._sink = self._build_sink()

    def _build_sink(self):
        if not self._enabled:
            return None
        if self._file_path:
            # Open in append mode so multiple workers / restarts don't clobber.
            try:
                handle = open(self._file_path, "a", buffering=1, encoding="utf-8")
                return lambda line: handle.write(line + "\n")
            except OSError:
                logger.warning(
                    "DEER_FLOW_STREAM_TRACE_FILE=%s could not be opened; falling back to stderr",
                    self._file_path,
                    exc_info=True,
                )
        return lambda line: sys.stderr.write(line + "\n")

    @property
    def enabled(self) -> bool:
        return self._enabled

    def record(
        self,
        stage: str,
        *,
        run_id: str | None = None,
        thread_id: str | None = None,
        event_id: str | None = None,
        extra: dict[str, Any] | None = None,
    ) -> None:
        if not self._enabled or self._sink is None:
            return
        try:
            with self._lock:
                self._seq += 1
                seq = self._seq
                monotonic_ns = time.monotonic_ns()
                wall_iso = time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime()) + f".{int((monotonic_ns % 1_000_000_000) / 1_000_000):03d}Z"
                payload: dict[str, Any] = {
                    "seq": seq,
                    "stage": stage,
                    "monotonic_ns": monotonic_ns,
                    "wall_iso": wall_iso,
                }
                rid = _sanitize(run_id)
                tid = _sanitize(thread_id)
                eid = _sanitize(event_id)
                if rid is not None:
                    payload["run_id"] = rid
                if tid is not None:
                    payload["thread_id"] = tid
                if eid is not None:
                    payload["event_id"] = eid
                if extra:
                    # JSON-coerce values so the line is always parseable.
                    safe_extra: dict[str, Any] = {}
                    for k, v in extra.items():
                        try:
                            json.dumps(v)
                            safe_extra[str(k)] = v
                        except (TypeError, ValueError):
                            safe_extra[str(k)] = repr(v)
                    payload["extra"] = safe_extra
                line = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
                self._ring.append(payload)
                self._sink(line)
        except Exception:
            logger.debug("stream trace record failed", exc_info=True)

    def read_recent(self, limit: int = 1000) -> list[dict[str, Any]]:
        if not self._enabled:
            return []
        with self._lock:
            return list(self._ring)[-limit:]


diagnostics = _Diagnostics()


# Public recording helpers ----------------------------------------------------
# These wrappers exist so call sites read like English and so the diagnostic
# internals can be refactored without touching every boundary.


def record_worker_publish(
    *,
    run_id: str,
    event: str,
    payload_bytes: int,
    monotonic_ns_at_astream: int,
) -> None:
    diagnostics.record(
        "worker.publish",
        run_id=run_id,
        extra={
            "event": event,
            "payload_bytes": payload_bytes,
            "astream_to_publish_latency_ms": (time.monotonic_ns() - monotonic_ns_at_astream) / 1_000_000,
        },
    )


def record_bridge_publish(
    *,
    run_id: str,
    event_id: str,
    event: str,
    buffer_size: int,
    start_offset: int,
    dropped_count: int,
) -> None:
    diagnostics.record(
        "bridge.publish",
        run_id=run_id,
        event_id=event_id,
        extra={
            "event": event,
            "buffer_size": buffer_size,
            "start_offset": start_offset,
            "dropped_count": dropped_count,
        },
    )


def record_bridge_subscribe_resolve(
    *,
    run_id: str,
    last_event_id: str | None,
    resolved_offset: int,
    start_offset: int,
    retained_events: int,
) -> None:
    diagnostics.record(
        "bridge.subscribe.resolve",
        run_id=run_id,
        event_id=last_event_id,
        extra={
            "resolved_offset": resolved_offset,
            "start_offset": start_offset,
            "retained_events": retained_events,
        },
    )


def record_bridge_subscribe_yield(
    *,
    run_id: str,
    kind: str,  # "event" | "heartbeat" | "end"
    event_id: str | None = None,
    subscriber_count: int | None = None,
) -> None:
    extra: dict[str, Any] = {"kind": kind}
    if subscriber_count is not None:
        extra["subscriber_count"] = subscriber_count
    diagnostics.record(
        "bridge.subscribe.yield",
        run_id=run_id,
        event_id=event_id,
        extra=extra,
    )


def record_sse_format(
    *,
    run_id: str,
    kind: str,  # "event" | "heartbeat" | "end"
    event_id: str | None,
    byte_count: int,
) -> None:
    diagnostics.record(
        "sse.format",
        run_id=run_id,
        event_id=event_id,
        extra={"kind": kind, "byte_count": byte_count},
    )


def record_sse_consumer_loop_iter(
    *,
    run_id: str,
    thread_id: str,
    iteration: int,
    disconnected: bool,
) -> None:
    diagnostics.record(
        "sse.consumer.iter",
        run_id=run_id,
        thread_id=thread_id,
        extra={"iteration": iteration, "disconnected": disconnected},
    )


def record_sse_consumer_disconnect(
    *,
    run_id: str,
    thread_id: str,
    kind: str,  # "request_disconnected" | "client_cancelled"
) -> None:
    diagnostics.record(
        "sse.consumer.disconnect",
        run_id=run_id,
        thread_id=thread_id,
        extra={"kind": kind},
    )


def record_bridge_subscribe_close(
    *,
    run_id: str,
    thread_id: str | None,
    reason: str,
) -> None:
    diagnostics.record(
        "bridge.subscribe.close",
        run_id=run_id,
        thread_id=thread_id,
        extra={"reason": reason},
    )


def read_recent_diagnostics(limit: int = 1000) -> list[dict[str, Any]]:
    return diagnostics.read_recent(limit)