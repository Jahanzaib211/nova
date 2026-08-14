"""Regression tests for ``ObserveAdjustMiddleware.aafter_tool``'s ``task_progress``
emission.

Pinned by the 2026-08-14 audit: ``task_progress`` is the primary way the
Agent's Computer renders the todo progress bar. Its shape is consumed by
``useThreadStream.onCustomEvent`` on the frontend; the contract fixture
``backend/contracts/custom_events_contract.json`` pins each payload's *shape*,
and this test pins the *producer*: that ``aafter_tool`` writes exactly the
documented fields with the documented values for the three relevant todo
states (none, partial, all-done).
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

import pytest

_HERE = Path(__file__).resolve().parent
_BACKEND = _HERE.parent
if str(_BACKEND) not in sys.path:
    sys.path.insert(0, str(_BACKEND))


def _build_middleware():
    """Build an ``ObserveAdjustMiddleware`` instance without going through the
    package's full DI graph. The middleware only uses ``self._try_record_hook``
    (no-op in this test) and otherwise delegates to module-level helpers, so
    constructing it directly is fine."""

    from deerflow.agents.middlewares.observe_adjust_middleware import (
        ObserveAdjustMiddleware,
    )

    return ObserveAdjustMiddleware()


def _capture_writer():
    """Return a fresh ``(list, writer_callable)`` pair. The writer is the
    callable LangGraph uses (``writer(dict)``); the list collects each dict
    in order."""

    events: list = []

    def writer(payload):
        events.append(payload)

    return events, writer


def _state(todos):
    """Build a state-like object exposing ``.get`` the way the middleware
    reads it."""
    return {"todos": todos, "messages": []}


def _config(writer):
    return {"configurable": {"thread_id": "t1"}, "writer": writer}


def test_emits_task_progress_when_todos_present():
    mw = _build_middleware()
    events, writer = _capture_writer()
    state = _state(
        todos=[
            {"status": "completed"},
            {"status": "in_progress"},
            {"status": "pending"},
        ]
    )
    asyncio.run(mw.aafter_tool(state, _config(writer)))
    task_progress = [e for e in events if e.get("type") == "task_progress"]
    assert task_progress == [{"type": "task_progress", "step": 1, "total": 3, "status": "in_progress"}]


def test_emits_completed_when_all_todos_done():
    mw = _build_middleware()
    events, writer = _capture_writer()
    state = _state(
        todos=[
            {"status": "completed"},
            {"status": "done"},
            {"status": "completed"},
        ]
    )
    asyncio.run(mw.aafter_tool(state, _config(writer)))
    task_progress = [e for e in events if e.get("type") == "task_progress"]
    assert task_progress == [{"type": "task_progress", "step": 3, "total": 3, "status": "completed"}]


def test_does_not_emit_task_progress_when_no_todos():
    mw = _build_middleware()
    events, writer = _capture_writer()
    state = _state(todos=[])
    asyncio.run(mw.aafter_tool(state, _config(writer)))
    task_progress = [e for e in events if e.get("type") == "task_progress"]
    assert task_progress == []


def test_does_not_emit_when_writer_is_not_callable():
    mw = _build_middleware()
    state = _state(todos=[{"status": "completed"}])
    # No ``writer`` in the config (LangGraph runs without a stream writer in
    # the embedded / unit-test path). The middleware must not raise.
    asyncio.run(mw.aafter_tool(state, {"configurable": {"thread_id": "t1"}}))
