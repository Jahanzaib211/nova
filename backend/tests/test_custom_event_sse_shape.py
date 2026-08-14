"""Regression tests for the ``bridge.publish(run_id, "custom", payload)`` →
``format_sse("custom", …)`` wire contract.

Pinned by the 2026-08-14 audit: the four custom-event payloads (task_progress,
verify_result, llm_error, task_running) flow backend → SSE → frontend
``onCustomEvent`` → React parser. The contract fixture
``backend/contracts/custom_events_contract.json`` pins each payload's *shape*;
this test pins the wire *frame* that carries it (``event: custom``, ``data: <json>``,
``id: <seq>``, blank line) so any future format drift in ``format_sse``
breaks the build instead of the frontend.
"""

from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path

import pytest

_HERE = Path(__file__).resolve().parent
_BACKEND = _HERE.parent
if str(_BACKEND) not in sys.path:
    sys.path.insert(0, str(_BACKEND))


def test_format_sse_emits_event_data_id_blank_lines():
    from app.gateway.services import format_sse

    frame = format_sse("custom", {"type": "task_progress", "step": 1, "total": 2, "status": "in_progress"}, event_id="42")
    # Field order: event, data, id, blank line, blank line — the
    # LangGraph Platform wire format that ``useStream`` decodes.
    lines = frame.split("\n")
    assert lines[0] == "event: custom"
    assert lines[1].startswith("data: ")
    payload = json.loads(lines[1][len("data: ") :])
    assert payload == {"type": "task_progress", "step": 1, "total": 2, "status": "in_progress"}
    assert lines[2] == "id: 42"
    assert lines[3] == ""
    assert lines[4] == ""


def test_format_sse_omits_id_when_none():
    from app.gateway.services import format_sse

    frame = format_sse("custom", {"type": "llm_error", "error_type": "Quota"})
    lines = frame.split("\n")
    assert lines[0] == "event: custom"
    assert lines[1].startswith("data: ")
    # No ``id:`` field when event_id is None.
    assert not any(ln.startswith("id:") for ln in lines)
    # Two trailing blank lines (one for the message terminator, one for the
    # trailing newline split).
    assert lines[-2] == ""
    assert lines[-1] == ""


def test_bridge_publish_routes_custom_payload_to_sse_consumer():
    """Drive the full path: ``bridge.publish(run_id, "custom", payload)`` →
    consume via the stream bridge → the ``custom`` events surface with the
    documented payload shape. (The full ``format_sse`` round-trip is covered
    by the first two tests in this file; this one pins the bridge side.)
    """

    from deerflow.runtime.stream_bridge.memory import MemoryStreamBridge

    async def _drive():
        bridge = MemoryStreamBridge()
        run_id = "r1"
        await bridge.publish(run_id, "custom", {"type": "task_progress", "step": 1, "total": 2, "status": "in_progress"})
        await bridge.publish(run_id, "custom", {"type": "verify_result", "ok": True, "routes": []})
        await bridge.publish(run_id, "values", {"messages": []})  # non-custom, must be ignored by ``custom`` filter
        await bridge.publish_end(run_id)

        # Drain the bridge's in-memory stream and only keep ``custom`` entries.
        custom = []
        async for entry in bridge.subscribe(run_id):
            if entry.event == "custom":
                custom.append(entry)
            elif entry.event == "end":
                break
        return [(e.event, e.data) for e in custom]

    frames = asyncio.run(_drive())
    assert [ev for ev, _ in frames] == ["custom", "custom"]
    assert frames[0][1] == {"type": "task_progress", "step": 1, "total": 2, "status": "in_progress"}
    assert frames[1][1] == {"type": "verify_result", "ok": True, "routes": []}
