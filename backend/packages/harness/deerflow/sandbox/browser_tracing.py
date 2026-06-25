"""Structured logging + OpenTelemetry-style spans for browser operations.

Two concerns, one module:

1. **Structured logging with trace_id** (B1):
   - Mirrors the ``trace_id`` pattern from ``agents/middlewares/task_tool.py``
     where the agent run loop sets a per-run trace_id that flows through
     every subsystem log line.
   - ``browser_log(event, **fields)`` emits a single line with consistent
     fields: ``trace_id``, ``thread_id``, ``span_id``, ``event``, plus
     arbitrary ``**fields``.
   - Falls back gracefully when no trace context is set (debug logs only).

2. **OpenTelemetry-style spans** (B6):
   - The full OTel SDK is overkill for this scope — we don't need a
     remote exporter, just structured parent/child relationships that
     can be cross-referenced in logs.
   - ``browser_span(name, **attributes)`` context manager creates a span
     with a unique span_id, parent_id, start/end timestamps, status.
   - Spans are pushed to a thread-local stack so child spans link to the
     most recent parent.
   - All span events are emitted via ``browser_log`` so they show up in
     the same log stream as everything else.

This module deliberately does NOT depend on opentelemetry-api — emitting
OTel-compatible JSON keeps the door open to add the SDK later without
changing callsites.
"""

from __future__ import annotations

import contextlib
import json
import logging
import os
import secrets
import threading
import time
import uuid
from contextvars import ContextVar
from dataclasses import dataclass, field
from typing import Any, Iterator

logger = logging.getLogger(__name__)

# ---------- trace context (per async task / per thread) ----------

# ContextVar so trace_id propagates through ``await`` boundaries in async code
# AND falls back to thread-local for sync worker threads (asyncio.to_thread).
_current_trace_id: ContextVar[str | None] = ContextVar("deerflow_trace_id", default=None)
_thread_trace_id = threading.local()


def set_trace_id(trace_id: str | None) -> None:
    """Set the trace_id for the current async context + thread.

    Calling with ``None`` clears it. Mirrors ``task_tool.py:271`` pattern.
    """
    _current_trace_id.set(trace_id)
    if trace_id is None:
        if hasattr(_thread_trace_id, "value"):
            delattr(_thread_trace_id, "value")
    else:
        _thread_trace_id.value = trace_id


def get_trace_id() -> str | None:
    """Return the current trace_id, or None if unset.

    Tries ContextVar first (correct for async), then thread-local
    (correct for sync workers spawned from async via ``asyncio.to_thread``).
    """
    trace = _current_trace_id.get()
    if trace is not None:
        return trace
    return getattr(_thread_trace_id, "value", None)


def new_trace_id() -> str:
    """Generate a new trace_id (32-char hex, like OpenTelemetry)."""
    return secrets.token_hex(16)


# ---------- span stack (parent/child) ----------

_current_spans: ContextVar[list["BrowserSpan"] | None] = ContextVar("deerflow_current_spans", default=None)


@dataclass
class BrowserSpan:
    """Minimal OpenTelemetry-shaped span.

    Schema mirrors the OTel semantic conventions enough that future
    migration to the SDK is mechanical: rename fields, drop this class,
    use opentelemetry.trace.Span.
    """

    name: str
    trace_id: str
    span_id: str
    parent_span_id: str | None
    start_time: float
    end_time: float | None = None
    status: str = "unset"  # unset | ok | error
    attributes: dict[str, Any] = field(default_factory=dict)
    events: list[dict[str, Any]] = field(default_factory=list)

    def set_attribute(self, key: str, value: Any) -> None:
        self.attributes[key] = value

    def add_event(self, name: str, **attributes: Any) -> None:
        self.events.append(
            {
                "name": name,
                "timestamp": time.time(),
                "attributes": dict(attributes),
            }
        )

    def set_status(self, status: str, *, message: str | None = None) -> None:
        self.status = status
        if message is not None:
            self.attributes["status_message"] = message

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "trace_id": self.trace_id,
            "span_id": self.span_id,
            "parent_span_id": self.parent_span_id,
            "start_time": self.start_time,
            "end_time": self.end_time,
            "duration_ms": round((self.end_time or time.time()) - self.start_time, 3) * 1000,
            "status": self.status,
            "attributes": self.attributes,
            "events": self.events,
        }


@contextlib.contextmanager
def browser_span(
    name: str,
    *,
    attributes: dict[str, Any] | None = None,
) -> Iterator[BrowserSpan]:
    """Context manager that creates a span and pushes it as the current parent.

    On exit, the span is finalised (end_time + status set) and emitted
    via ``browser_log`` at DEBUG level so it shows up in the same log
    stream as everything else.

    The span's parent is the most recent active span in the same async
    context (or None for root spans). After exit, the previous parent
    is restored.
    """
    trace_id = get_trace_id() or new_trace_id()
    stack = _current_spans.get() or []
    parent = stack[-1] if stack else None
    span = BrowserSpan(
        name=name,
        trace_id=trace_id,
        span_id=secrets.token_hex(8),
        parent_span_id=parent.span_id if parent else None,
        start_time=time.time(),
        attributes=dict(attributes or {}),
    )
    new_stack = stack + [span]
    token = _current_spans.set(new_stack)
    try:
        yield span
    except Exception as exc:
        span.set_status("error", message=f"{type(exc).__name__}: {exc}")
        span.add_event("exception", type=type(exc).__name__, message=str(exc))
        raise
    else:
        span.set_status("ok")
    finally:
        span.end_time = time.time()
        _current_spans.reset(token)
        _emit_span(span)


def current_span() -> BrowserSpan | None:
    """Return the current (innermost) span, or None if no span is active."""
    stack = _current_spans.get()
    if not stack:
        return None
    return stack[-1]


# ---------- structured logging ----------

def browser_log(
    event: str,
    *,
    level: int = logging.INFO,
    thread_id: str | None = None,
    span: BrowserSpan | None = None,
    exc_info: bool = False,
    **fields: Any,
) -> None:
    """Emit a structured log line for a browser subsystem event.

    Fields included automatically:
      - ``event``: the event name (e.g. "browser_check.start")
      - ``trace_id``: current trace_id, if any
      - ``span_id``: current span_id, if any
      - ``parent_span_id``: current span's parent, if any
      - ``thread_id``: passed in or auto-attached from call site
      - all ``**fields`` passed by the caller

    Output format: JSON dict appended to the log message, so log
    aggregators that parse JSON can index the fields without regex.
    Falls back to a compact ``key=value`` format if JSON serialization
    fails on a field value.
    """
    payload: dict[str, Any] = {"event": event}
    trace = get_trace_id()
    if trace:
        payload["trace_id"] = trace
    sp = span or current_span()
    if sp is not None:
        payload["span_id"] = sp.span_id
        if sp.parent_span_id:
            payload["parent_span_id"] = sp.parent_span_id
        # Span-scoped attributes are merged into the log payload so a single
        # log line is self-describing — no need to cross-reference spans.
        for k, v in sp.attributes.items():
            payload.setdefault(f"span.{k}", v)
    if thread_id:
        payload["thread_id"] = thread_id
    for k, v in fields.items():
        if v is None:
            continue
        payload[k] = v

    try:
        rendered = json.dumps(payload, default=str, sort_keys=True)
    except (TypeError, ValueError):
        rendered = " ".join(f"{k}={v!r}" for k, v in payload.items() if v is not None)

    logger.log(level, rendered, exc_info=exc_info)


def _emit_span(span: BrowserSpan) -> None:
    """Emit a completed span as a structured log line."""
    browser_log(
        f"span.{span.name}",
        level=logging.DEBUG,
        span=span,
        span_lifecycle="end",
        duration_ms=round((span.end_time - span.start_time) * 1000, 3),
        status=span.status,
        events=span.events,
    )