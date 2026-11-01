"""One stuck write must never silently switch a run's journal off.

Run 3cc50519 (2026-11-01) ran for over an hour with 626 messages and 68M
tokens counted in memory, yet the ``run_events`` table held **zero** rows for
it — not even its ``run.start`` trace — so the ops console showed "No
messages in this thread", and its progress snapshot froze at 12:46:51 while
the run kept working. Every other run on the same gateway journaled normally,
and nothing was logged.

The journal gates each new flush on "the previous flush task is done". A write
that never returns (a hung connection, a task parked on an event loop that no
longer runs it) therefore disabled all later persistence for the run, with no
error and no retry — and the worker's ``finally`` would then wait on that same
task forever. These tests pin the three properties that prevent it: writes are
bounded, a task that cannot finish does not gate the current loop, and the
final flush is bounded.
"""

import asyncio
import logging

import pytest

from deerflow.runtime.events.store.memory import MemoryRunEventStore
from deerflow.runtime.journal import RunJournal

pytestmark = pytest.mark.anyio


class HangsFirstWrite(MemoryRunEventStore):
    """The first ``put_batch`` never returns; later ones work."""

    def __init__(self) -> None:
        super().__init__()
        self.calls = 0

    async def put_batch(self, events):
        self.calls += 1
        if self.calls == 1:
            await asyncio.Event().wait()
        return await super().put_batch(events)


class AlwaysHangs(MemoryRunEventStore):
    async def put_batch(self, events):
        await asyncio.Event().wait()


def _emit(journal: RunJournal, n: int, start: int = 0) -> None:
    for i in range(start, start + n):
        journal._put(event_type="ai_message", category="message", content=f"m{i}")


async def _settle(seconds: float = 0.3) -> None:
    await asyncio.sleep(seconds)


async def _contents(store: MemoryRunEventStore) -> list[str]:
    rows = await store.list_messages("t1", limit=100)
    return [r["content"] for r in rows]


async def test_a_hung_write_is_retried_not_dropped(caplog):
    store = HangsFirstWrite()
    journal = RunJournal("r1", "t1", store, flush_threshold=2, write_timeout=0.1)

    with caplog.at_level(logging.WARNING, logger="deerflow.runtime.journal"):
        _emit(journal, 2)  # first flush hangs, then times out
        await _settle()
        _emit(journal, 2, start=2)  # must still flush, carrying the failed batch with it
        await _settle()

    assert await _contents(store) == ["m0", "m1", "m2", "m3"]
    assert any("timed out" in r.getMessage() for r in caplog.records), "a stalled write must be visible in the log"


async def test_a_task_parked_on_another_loop_does_not_gate_this_one():
    store = MemoryRunEventStore()
    journal = RunJournal("r1", "t1", store, flush_threshold=2, write_timeout=1)

    other = asyncio.new_event_loop()
    try:
        # A flush scheduled on a loop that never runs it again — it can
        # never complete, so it must not block flushes on the run's loop.
        parked = other.create_task(asyncio.sleep(3600))
        journal._pending_flush_tasks.add(parked)

        _emit(journal, 2)
        await _settle()
        assert await _contents(store) == ["m0", "m1"]
    finally:
        # The other loop cannot be run from inside this one; cancelling the
        # never-started task and closing its loop is all the cleanup it needs.
        parked.cancel()
        other.close()


async def test_final_flush_is_bounded_when_the_store_never_answers():
    journal = RunJournal("r1", "t1", AlwaysHangs(), flush_threshold=100, write_timeout=0.1)
    _emit(journal, 3)
    # The worker's ``finally`` awaits this; it must end, with an error the
    # worker already logs, rather than keep the run from ever finishing.
    with pytest.raises(TimeoutError):
        await asyncio.wait_for(journal.flush(), timeout=5)


async def test_final_flush_waits_for_a_slow_but_live_write():
    """Bounded is not the same as impatient: a write that completes within
    the bound must land, not be abandoned."""

    class Slow(MemoryRunEventStore):
        async def put_batch(self, events):
            await asyncio.sleep(0.05)
            return await super().put_batch(events)

    store = Slow()
    journal = RunJournal("r1", "t1", store, flush_threshold=2, write_timeout=1)
    _emit(journal, 3)  # two flushed in the background, one left in the buffer
    await journal.flush()
    assert await _contents(store) == ["m0", "m1", "m2"]


async def test_a_hung_progress_write_does_not_freeze_progress():
    calls: list[dict] = []
    hang_first = {"armed": True}

    async def reporter(snapshot: dict) -> None:
        if hang_first.pop("armed", False):
            await asyncio.Event().wait()
        calls.append(snapshot)

    journal = RunJournal("r1", "t1", MemoryRunEventStore(), progress_reporter=reporter, progress_flush_interval=0.0, write_timeout=0.1)
    journal._schedule_progress_flush()  # hangs, then times out
    await _settle()
    journal._schedule_progress_flush()  # must be allowed to run
    await _settle()
    assert calls, "progress stayed frozen behind one stuck write"
