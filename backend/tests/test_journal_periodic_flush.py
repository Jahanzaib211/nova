"""A run stuck in one long tool call still proves it is alive.

Heartbeats were only written from ``_put`` (when some event arrived) and the
buffer only flushed at the 20-event threshold, so a run parked in a single
long tool call emitted nothing at all: no heartbeat, and whatever events were
buffered below the threshold stayed in memory. The journal now arms an
owner-loop timer that writes a heartbeat and flushes on wall-clock time while
the run is active, and the final ``flush()`` cancels it.
"""

from __future__ import annotations

import asyncio
import time

import pytest

from nova.runtime import journal as journal_mod
from nova.runtime.events.store.memory import MemoryRunEventStore
from nova.runtime.journal import RunJournal

pytestmark = pytest.mark.anyio


@pytest.fixture
def anyio_backend():
    return "asyncio"


@pytest.fixture
def fast_heartbeat(monkeypatch):
    monkeypatch.setattr(journal_mod, "HEARTBEAT_INTERVAL_SECONDS", 0.05)


async def _events(store: MemoryRunEventStore, run_id: str = "r1") -> list[dict]:
    return await store.list_events("t1", run_id, limit=1000)


async def _settle(seconds: float) -> None:
    await asyncio.sleep(seconds)
    # let scheduled flush tasks complete
    for _ in range(5):
        await asyncio.sleep(0)


async def test_a_silent_run_still_writes_heartbeats(fast_heartbeat):
    store = MemoryRunEventStore()
    journal = RunJournal("r1", "t1", store, liveness_interval=0.05)

    # Nothing happens -- the run is inside one long tool call.
    await _settle(0.3)

    beats = [e for e in await _events(store) if e["event_type"] == "run.heartbeat"]
    assert len(beats) >= 2, "the timer must write heartbeats with no events at all"
    meta = beats[-1]["metadata"]
    assert meta["source"] == "timer"
    assert meta["idle_seconds"] > 0
    await journal.flush()


async def test_buffered_events_below_threshold_are_flushed_on_time(fast_heartbeat):
    store = MemoryRunEventStore()
    journal = RunJournal("r1", "t1", store, flush_threshold=20, liveness_interval=0.05)

    journal._put(event_type="llm.human.input", category="message", content="hi")
    assert await _events(store) == [], "below threshold: nothing written yet"

    await _settle(0.15)
    types = [e["event_type"] for e in await _events(store)]
    assert "llm.human.input" in types
    await journal.flush()


async def test_timer_heartbeat_does_not_count_as_activity(fast_heartbeat):
    store = MemoryRunEventStore()
    journal = RunJournal("r1", "t1", store, liveness_interval=0.05)
    await _settle(0.2)
    assert journal.last_activity_age_seconds >= 0.15, "a watchdog must still see the run as idle"
    await journal.flush()


async def test_final_flush_cancels_the_timer(fast_heartbeat):
    store = MemoryRunEventStore()
    journal = RunJournal("r1", "t1", store, liveness_interval=0.05)
    await journal.flush()
    count = len(await _events(store))

    await _settle(0.25)
    assert len(await _events(store)) == count, "no heartbeats after the run ended"
    assert journal._liveness_handle is None


async def test_busy_runs_are_not_double_heartbeated():
    """The timer only writes when no heartbeat went out for a full interval."""
    store = MemoryRunEventStore()
    journal = RunJournal("r1", "t1", store, liveness_interval=0.05)
    journal._last_heartbeat_mono = time.monotonic()  # one just went out via _put
    journal._liveness_tick()
    await _settle(0.01)
    assert [e for e in await _events(store) if e["event_type"] == "run.heartbeat"] == []
    await journal.flush()


async def test_disabled_interval_arms_nothing():
    journal = RunJournal("r1", "t1", MemoryRunEventStore(), liveness_interval=None)
    assert journal._liveness_handle is None
    await journal.flush()


def test_no_owner_loop_no_timer():
    journal = RunJournal("r1", "t1", MemoryRunEventStore())
    assert journal._owner_loop is None
    assert journal._liveness_handle is None


async def test_default_interval_is_one_minute():
    journal = RunJournal("r1", "t1", MemoryRunEventStore())
    try:
        assert journal._liveness_interval == 60.0
        assert journal._liveness_handle is not None
    finally:
        await journal.flush()
