"""Tests for Phase C0.2 — SSE comment emission of correlation_id.

Verifies that ``sse_consumer`` emits the run's correlation_id as the first
SSE frame (an SSE comment) so the frontend can pin it before any event
arrives. Uses a synthetic bridge so no live runtime is required.
"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest

from app.gateway.services import sse_consumer
from deerflow.runtime.runs.manager import RunManager, RunRecord, RunStatus
from deerflow.runtime.runs.store.memory import MemoryRunStore


class _FakeBridge:
    """In-memory bridge that yields a single END_SENTINEL."""

    def __init__(self) -> None:
        self.subscribed: list[str] = []

    async def subscribe(self, run_id, last_event_id=None):
        self.subscribed.append(run_id)
        from deerflow.runtime import END_SENTINEL

        yield END_SENTINEL


class _FakeRequest:
    """Minimal stand-in for a FastAPI Request; never reports disconnected."""

    async def is_disconnected(self) -> bool:
        return False

    headers: dict = {}


@pytest.mark.anyio
async def test_sse_consumer_emits_correlation_id_as_first_frame():
    mgr = RunManager()
    record = await mgr.create("thread-a")
    bridge = _FakeBridge()
    request = _FakeRequest()

    out = []
    async for frame in sse_consumer(bridge, record, request, mgr):  # type: ignore[arg-type]
        out.append(frame)
        if len(out) >= 2:
            break

    # First frame MUST be the correlation_id comment.
    first = out[0]
    assert first.startswith(":"), f"first frame must be an SSE comment, got {first!r}"
    assert f"correlation_id={record.correlation_id}" in first


@pytest.mark.anyio
async def test_sse_consumer_omits_correlation_id_when_empty():
    """Legacy runs (no correlation_id) must not emit a malformed comment."""
    mgr = RunManager()
    # Construct a record manually so correlation_id stays empty.
    record = RunRecord(
        run_id="legacy-run",
        thread_id="thread-a",
        assistant_id=None,
        status=RunStatus.success,
        on_disconnect=__import__("deerflow.runtime.runs.schemas", fromlist=["DisconnectMode"]).DisconnectMode.cancel,
        correlation_id="",
    )
    bridge = _FakeBridge()
    request = _FakeRequest()

    out = []
    async for frame in sse_consumer(bridge, record, request, mgr):  # type: ignore[arg-type]
        out.append(frame)
        if len(out) >= 2:
            break

    # First frame should NOT be a correlation_id comment.
    assert not out[0].startswith(": correlation_id="), f"empty correlation_id must not emit a comment frame, got {out[0]!r}"


@pytest.mark.anyio
async def test_correlation_id_round_trips_through_sse_consumer():
    """End-to-end: the comment emitted at the start of the SSE stream
    carries the same correlation_id that the backend assigned at create()
    time.
    """
    mgr = RunManager()
    record = await mgr.create("thread-a")
    bridge = _FakeBridge()
    request = _FakeRequest()

    frames = []
    async for frame in sse_consumer(bridge, record, request, mgr):  # type: ignore[arg-type]
        frames.append(frame)

    corr_frame = next(f for f in frames if f.startswith(": correlation_id="))
    corr = corr_frame.split("=", 1)[1].strip()
    assert corr == record.correlation_id
