"""A run journal writes only on the event loop that owns it.

Subagents run on their own long-lived event loop in another thread, and they
inherit the lead run's callbacks. With ``RunJournal.run_inline = True`` the
journal's callbacks therefore fire ON the subagent's loop, and the journal
scheduled its store writes and progress snapshots there. The SQLAlchemy engine
behind both belongs to the gateway's loop: every such write died with
"Exception terminating connection ... Task pending", and the asyncpg
connection it had opened could not be closed, so it leaked. One leak per
5-second progress interval exhausted Postgres' 100 slots in six minutes
(2026-11-01 21:07 -> 21:13 UTC) and the database refused every client for ten
hours.
"""

from __future__ import annotations

import asyncio
import threading

from deerflow.runtime.journal import RunJournal


class _LoopRecordingStore:
    """An event store that records which loop each write ran on."""

    def __init__(self) -> None:
        self.write_loops: list[asyncio.AbstractEventLoop] = []
        self.events: list[dict] = []

    async def put_batch(self, batch: list[dict]) -> None:
        self.write_loops.append(asyncio.get_running_loop())
        self.events.extend(batch)


def _fire_from_a_subagent_loop(journal: RunJournal, events: int) -> None:
    """Emit ``events`` journal events from a second loop on a second thread."""

    async def _subagent() -> None:
        for i in range(events):
            journal._put(event_type="llm.ai.response", category="trace", content=f"e{i}")
            journal._schedule_progress_flush()
            await asyncio.sleep(0)

    thread = threading.Thread(target=lambda: asyncio.run(_subagent()))
    thread.start()
    thread.join(timeout=10)
    assert not thread.is_alive()


def test_writes_from_a_subagent_loop_run_on_the_owning_loop() -> None:
    store = _LoopRecordingStore()
    reporter_loops: list[asyncio.AbstractEventLoop] = []

    async def reporter(snapshot: dict) -> None:
        reporter_loops.append(asyncio.get_running_loop())

    async def _run() -> asyncio.AbstractEventLoop:
        owner = asyncio.get_running_loop()
        journal = RunJournal("run-x", "thread-x", store, flush_threshold=5, progress_reporter=reporter, progress_flush_interval=0.0)
        await asyncio.to_thread(_fire_from_a_subagent_loop, journal, 23)
        # Let the marshalled writes run on this loop, then the final flush.
        for _ in range(20):
            await asyncio.sleep(0.01)
        await journal.flush()
        return owner

    owner = asyncio.run(_run())

    assert store.write_loops, "mid-run writes from the subagent loop were never made"
    assert all(loop is owner for loop in store.write_loops), "a store write ran on the subagent's loop"
    assert reporter_loops, "progress from the subagent loop was never reported"
    assert all(loop is owner for loop in reporter_loops), "a progress write ran on the subagent's loop"
    contents = [e["content"] for e in store.events if e["event_type"] == "llm.ai.response"]
    assert contents == [f"e{i}" for i in range(23)], "events were lost or reordered across the loop hand-off"


def test_a_journal_built_outside_any_loop_keeps_the_old_behaviour() -> None:
    """Embedded callers and tests construct journals with no loop running; the
    journal then writes on whichever loop calls it, as before."""
    store = _LoopRecordingStore()
    journal = RunJournal("run-y", "thread-y", store, flush_threshold=2)

    async def _run() -> asyncio.AbstractEventLoop:
        journal._put(event_type="run.start", category="trace")
        journal._put(event_type="run.end", category="trace")
        await asyncio.sleep(0.01)
        await journal.flush()
        return asyncio.get_running_loop()

    loop = asyncio.run(_run())
    assert store.write_loops and all(w is loop for w in store.write_loops)
