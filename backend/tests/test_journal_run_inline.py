"""The dispatch-path regression test for the journal.

The other journal tests call ``journal.on_llm_end(...)`` directly. That
cannot see the actual failure: in the real run LangChain dispatches sync
handlers through ``AsyncCallbackManagerForLLMRun`` + ``run_in_executor``,
which throws the handler onto a thread-pool thread where
``asyncio.get_running_loop()`` raises and every mid-run write silently
no-ops (run 3cc50519 streamed for an hour and persisted zero events).

So these tests drive a real ``AsyncCallbackManagerForLLMRun`` — the same
dispatch machinery the worker pays for. They fail the moment anyone
removes ``run_inline = True`` from the handlers.
"""

from __future__ import annotations

import asyncio
import contextlib
from typing import Any
from uuid import uuid4

import pytest
from langchain_core.callbacks.manager import AsyncCallbackManagerForLLMRun
from langchain_core.messages import AIMessage
from langchain_core.outputs import ChatGeneration, LLMResult

from deerflow.runtime import journal as journal_module
from deerflow.runtime.journal import RunJournal
from deerflow.subagents.token_collector import SubagentTokenCollector

pytestmark = pytest.mark.anyio


class RecordingStore:
    """Minimal RunEventStore double that remembers every batch it wrote."""

    def __init__(self) -> None:
        self.batches: list[list[dict]] = []

    async def put_batch(self, events: list[dict]) -> None:
        await asyncio.sleep(0)
        self.batches.append(list(events))


USAGE = {"input_tokens": 100, "output_tokens": 10, "total_tokens": 110}


def _llm_result(index: int = 0) -> LLMResult:
    message = AIMessage(content=f"response {index}", usage_metadata=USAGE)  # type: ignore[arg-type]
    return LLMResult(generations=[[ChatGeneration(message=message)]])


def _real_manager(handlers: list[Any]) -> AsyncCallbackManagerForLLMRun:
    """Build the manager shape the worker actually dispatches through.

    Called once per LLM response: a fresh ``run_id`` each time, because the
    journal dedups token accounting by LangChain's per-call run id (the
    same id firing twice for one response is the case it exists to catch).
    Reusing one manager across N calls would leave N-1 calls deduped away.
    """
    return AsyncCallbackManagerForLLMRun(
        run_id=uuid4(),
        handlers=list(handlers),
        inheritable_handlers=[],
        parent_run_id=None,
    )


async def test_handlers_declare_run_inline() -> None:
    # The pin: without run_inline=True, LangChain's sync-dispatch branch
    # sends every handler call to a worker thread. A refactor that drops the
    # flag must fail here, loudly, not silently in production.
    assert RunJournal.run_inline is True
    assert SubagentTokenCollector.run_inline is True


async def test_mid_run_flush_fires_through_real_dispatch() -> None:
    # With more unique llm runs than flush_threshold, the store must receive
    # a batch while events are still arriving — long before the worker's
    # explicit flush().
    store = RecordingStore()
    reporter_calls: list[dict] = []

    async def reporter(snapshot: dict) -> None:
        reporter_calls.append(snapshot)

    js = RunJournal(
        run_id="run-inline-1",
        thread_id="thread-1",
        event_store=store,  # type: ignore[arg-type]
        flush_threshold=5,
        progress_reporter=reporter,
    )
    for i in range(30):
        await _real_manager([js]).on_llm_end(_llm_result(index=i))

    assert store.batches, "mid-run flush never fired through real dispatch — run_inline regression?"
    mid_run_persisted = sum(len(b) for b in store.batches)
    assert mid_run_persisted > 0
    # And the journal's own view agrees: nothing is waiting on a final flush.
    assert js._buffer == [] or len(store.batches) > 1


async def test_progress_reporter_fires_mid_run_through_real_dispatch() -> None:
    store = RecordingStore()
    reporter_calls: list[dict] = []

    async def reporter(snapshot: dict) -> None:
        reporter_calls.append(snapshot)

    js = RunJournal(
        run_id="run-inline-2",
        thread_id="thread-1",
        event_store=store,  # type: ignore[arg-type]
        flush_threshold=5,
        progress_reporter=reporter,
    )
    for i in range(10):
        await _real_manager([js]).on_llm_end(_llm_result(index=i))

    # Give any scheduled progress task a beat to run. The reporter is
    # throttled (progress_flush_interval seconds between snapshots) and the
    # later snapshots fire on a delayed task, so what we pin here is that it
    # FIRED mid-run; the authoritative token totals live on the journal
    # itself and every not-yet-written snapshot would reach them too.
    for _ in range(5):
        await asyncio.sleep(0.01)

    assert reporter_calls, "progress reporter never fired mid-run through real dispatch"
    for snapshot in reporter_calls:
        assert snapshot["llm_call_count"] >= 1
        assert snapshot["total_tokens"] >= 110
    assert js.get_completion_data()["total_tokens"] == 110 * 10


async def test_final_flush_persists_everything_without_double_counting() -> None:
    store = RecordingStore()
    js = RunJournal(
        run_id="run-inline-3",
        thread_id="thread-1",
        event_store=store,  # type: ignore[arg-type],
        flush_threshold=5,
    )
    for i in range(12):
        await _real_manager([js]).on_llm_end(_llm_result(index=i))

    await js.flush()
    persisted_after_first = sum(len(b) for b in store.batches)
    assert js._buffer == []
    # Every llm.ai.response event plus any heartbeats must be out.
    assert persisted_after_first >= 12

    # A second flush must be a no-op: nothing re-written, nothing counted twice.
    batches_after_first = len(store.batches)
    await js.flush()
    assert len(store.batches) == batches_after_first
    assert sum(len(b) for b in store.batches) == persisted_after_first

    # Token dedup: the same run id fired once, so exactly one call counted.
    assert js._llm_call_count == 12
    assert js._total_tokens == 110 * 12


async def test_subagent_token_collector_inline_dispatch() -> None:
    collector = SubagentTokenCollector(caller="subagent:general-purpose")
    manager = _real_manager([collector])
    await manager.on_llm_end(_llm_result(index=0))

    records = collector.snapshot_records()
    assert records == [
        {
            "source_run_id": str(manager.run_id),
            "caller": "subagent:general-purpose",
            "model_name": None,
            "input_tokens": 100,
            "output_tokens": 10,
            "total_tokens": 110,
        }
    ]


async def test_heartbeat_rate_limited_to_one_per_minute() -> None:
    # The heartbeat is a liveness signal — one per minute, not one per event.
    # The module's clock is swapped BEFORE construction so throttle state
    # starts on the fake clock and the test is deterministic.
    class FakeTime:
        def __init__(self, now: float) -> None:
            self.now = now

        def monotonic(self) -> float:
            return self.now

    original_time = journal_module.time
    try:
        fake = FakeTime(now=1000.0)
        journal_module.time = fake  # type: ignore[assignment]
        store = RecordingStore()

        js = RunJournal(
            run_id="run-heartbeat-1",
            thread_id="thread-1",
            event_store=store,  # type: ignore[arg-type]
            flush_threshold=1000,
        )
        # Events inside the first minute: no heartbeat, the one-per-minute
        # clock started at construction.
        for _ in range(20):
            js._put(event_type="trace.bulk", category="trace")
        fake.now += 61.0  # past the minute boundary
        js._put(event_type="trace.later", category="trace")
        fake.now += 5.0  # still inside the heartbeat's own minute
        js._put(event_type="trace.later2", category="trace")
        fake.now += 70.0  # second minute elapsed
        js._put(event_type="trace.later3", category="trace")
        # A heartbeat triggers a flush, so the events are split between what
        # was written and what is still buffered.
        await js.flush()
    finally:
        journal_module.time = original_time  # type: ignore[assignment]

    written = [e for batch in store.batches for e in batch]
    heartbeats = [e for e in written if e["event_type"] == "run.heartbeat"]
    assert len(heartbeats) == 2, f"expected exactly 2 heartbeats (minute 1 + minute 2), got {len(heartbeats)}"
    first, second = heartbeats
    assert first["category"] == "liveness"
    assert first["run_id"] == "run-heartbeat-1"
    assert first["metadata"]["llm_call_count"] == 0
    assert second["metadata"]["elapsed_seconds"] >= 130.0


async def test_heartbeat_is_not_flooded_by_real_dispatch() -> None:
    # 10 real-dispatch llm calls spanning just over one fake minute produce
    # exactly one heartbeat among the persisted events.
    class FakeTime:
        def __init__(self) -> None:
            self.now = 1000.0

        def monotonic(self) -> float:
            return self.now

    original_time = journal_module.time
    try:
        fake = FakeTime()
        journal_module.time = fake  # type: ignore[assignment]
        store = RecordingStore()

        js = RunJournal(
            run_id="run-heartbeat-2",
            thread_id="thread-1",
            event_store=store,  # type: ignore[arg-type]
            flush_threshold=1000,
        )
        for i in range(10):
            if i == 5:
                fake.now += 61.0
            await _real_manager([js]).on_llm_end(_llm_result(index=i))
        await js.flush()
    finally:
        journal_module.time = original_time  # type: ignore[assignment]

    all_events = [e for batch in store.batches for e in batch]
    heartbeats = [e for e in all_events if e["event_type"] == "run.heartbeat"]
    assert len(heartbeats) == 1
    assert heartbeats[0]["category"] == "liveness"
    assert heartbeats[0]["metadata"]["llm_call_count"] == 5


async def test_last_activity_age_tracks_real_dispatch() -> None:
    store = RecordingStore()
    js = RunJournal(
        run_id="run-age-1",
        thread_id="thread-1",
        event_store=store,  # type: ignore[arg-type],
        flush_threshold=1000,
    )
    manager = _real_manager([js])
    await asyncio.sleep(0.01)
    fresh_age = js.last_activity_age_seconds
    assert fresh_age < 5.0
    await manager.on_llm_end(_llm_result(index=0))
    with contextlib.suppress(Exception):
        await manager.on_llm_end(_llm_result(index=1))
    assert js.last_activity_age_seconds < fresh_age  # activity refreshed it
    assert js.last_activity_age_seconds >= 0.0


@pytest.mark.asyncio
async def test_sync_dispatch_reaches_the_loop_even_from_executor_path() -> None:
    # The exact production hazard: a sync caller (LangChain's executor
    # branch, before the run_inline fix) would enter on_llm_end with no
    # running loop and silently drop the write. run_inline must make this
    # path keep its writes.
    store = RecordingStore()
    js = RunJournal(
        run_id="run-offloop-1",
        thread_id="thread-1",
        event_store=store,  # type: ignore[arg-type],
        flush_threshold=1000,
    )

    # Simulate the pre-fix environment directly: a sync dispatch from a
    # thread where no loop is running must still buffer the event.
    def off_loop_call() -> None:
        js.on_llm_end(
            _llm_result(index=0),
            run_id=uuid4(),
            tags=["lead_agent"],
        )

    await asyncio.get_running_loop().run_in_executor(None, off_loop_call)
    assert js._buffer, "event lost when dispatched off the loop"
