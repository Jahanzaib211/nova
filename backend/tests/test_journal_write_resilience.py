"""A journal whose store is down must back off, stay bounded, and not duplicate.

Three failure shapes, all reproduced before the fix:

* A store that fails fast (connection refused, pool exhausted) put the journal
  into a tight retry loop: ``_flush_async`` returned the batch to the buffer,
  ``_on_flush_done`` saw the buffer still at threshold and re-flushed at once —
  1699 attempts in 0.2 s, one CPU core pinned and a warning per attempt.
* A write that timed out *after* the store committed was re-sent, and the
  store gave the copy fresh ``seq`` numbers: duplicated events, no error.
* A heartbeat sat in the buffer until 20 events had accumulated, so a run
  stuck in a long tool call never got its liveness into the store.
"""

import asyncio
import logging
import time

import pytest

from deerflow.runtime import journal as journal_module
from deerflow.runtime.events.store.memory import MemoryRunEventStore
from deerflow.runtime.journal import RunJournal

pytestmark = pytest.mark.anyio


class FailsFast(MemoryRunEventStore):
    """Raises immediately for the first ``failures`` calls, then works."""

    def __init__(self, failures: int = 10**9) -> None:
        super().__init__()
        self.failures = failures
        self.calls = 0

    async def put_batch(self, events):
        self.calls += 1
        if self.calls <= self.failures:
            raise ConnectionError("store unavailable")
        return await super().put_batch(events)


class CommitsThenHangs(MemoryRunEventStore):
    """The first write commits and then never acknowledges; later ones work."""

    def __init__(self) -> None:
        super().__init__()
        self.calls = 0

    async def put_batch(self, events):
        self.calls += 1
        rows = await super().put_batch(events)
        if self.calls == 1:
            await asyncio.Event().wait()
        return rows


def _emit(journal: RunJournal, n: int, start: int = 0) -> None:
    for i in range(start, start + n):
        journal._put(event_type="ai_message", category="message", content=f"m{i}")


async def _contents(store: MemoryRunEventStore) -> list[str]:
    rows = await store.list_messages("t1", limit=1000)
    return [r["content"] for r in rows]


@pytest.fixture
def fast_backoff(monkeypatch):
    monkeypatch.setattr(journal_module, "RETRY_BASE_DELAY_SECONDS", 0.05)
    monkeypatch.setattr(journal_module, "RETRY_MAX_DELAY_SECONDS", 0.2)


async def test_a_fail_fast_store_does_not_spin():
    store = FailsFast()
    journal = RunJournal("r1", "t1", store, flush_threshold=2, write_timeout=1)

    _emit(journal, 2)
    # Events keep arriving while the store is down; none of them may bypass
    # the backoff and turn into an immediate write.
    for i in range(10):
        await asyncio.sleep(0.1)
        _emit(journal, 5, start=10 + i * 5)

    # Default backoff (0.5 s, doubling) allows the first attempt plus about
    # two retries in ~1 s; the tight loop managed thousands.
    assert store.calls <= 5, f"{store.calls} write attempts in ~1 s"
    journal._cancel_retry()


async def test_backoff_doubles_and_is_capped(fast_backoff):
    store = FailsFast()
    journal = RunJournal("r1", "t1", store, flush_threshold=2, write_timeout=1)
    _emit(journal, 2)
    await asyncio.sleep(1.0)
    # 0.05 + 0.1 + 0.2 + 0.2 + ... -> roughly 6-7 attempts in 1 s, not 1000s.
    assert 3 <= store.calls <= 10, store.calls
    assert journal._next_retry_delay() == pytest.approx(0.2)
    journal._cancel_retry()


async def test_failure_warnings_are_rate_limited(fast_backoff, caplog):
    store = FailsFast()
    journal = RunJournal("r1", "t1", store, flush_threshold=2, write_timeout=1)
    with caplog.at_level(logging.WARNING, logger="deerflow.runtime.journal"):
        _emit(journal, 2)
        await asyncio.sleep(2.5)
    journal._cancel_retry()
    warnings = [r for r in caplog.records if r.levelno == logging.WARNING and "Failed to flush" in r.getMessage()]
    assert store.calls >= 10
    assert 1 <= len(warnings) < store.calls / 3, (len(warnings), store.calls)
    assert "1 consecutive" in warnings[0].getMessage()


async def test_the_journal_recovers_when_the_store_comes_back(fast_backoff, caplog):
    store = FailsFast(failures=3)
    journal = RunJournal("r1", "t1", store, flush_threshold=2, write_timeout=1)
    with caplog.at_level(logging.INFO, logger="deerflow.runtime.journal"):
        _emit(journal, 2)
        await asyncio.sleep(1.0)
    assert await _contents(store) == ["m0", "m1"]
    assert journal._consecutive_failures == 0
    assert any("recovered" in r.getMessage() for r in caplog.records)

    # Back to normal: a threshold flush goes out immediately again.
    _emit(journal, 2, start=2)
    await asyncio.sleep(0.05)
    assert await _contents(store) == ["m0", "m1", "m2", "m3"]


async def test_final_flush_terminates_when_the_store_fails_fast(caplog):
    store = FailsFast()
    journal = RunJournal("r1", "t1", store, flush_threshold=2, write_timeout=0.1)
    _emit(journal, 7)
    await asyncio.sleep(0)

    start = time.monotonic()
    with caplog.at_level(logging.ERROR, logger="deerflow.runtime.journal"):
        with pytest.raises(ConnectionError):
            await asyncio.wait_for(journal.flush(), timeout=5)
    elapsed = time.monotonic() - start

    # Deadline is 3 x write_timeout; allow scheduling slack.
    assert elapsed < 1.0, elapsed
    assert store.calls < 20, store.calls
    errors = [r.getMessage() for r in caplog.records if r.levelno == logging.ERROR]
    assert any("7 event(s) undelivered" in m for m in errors), errors


async def test_final_flush_retries_within_its_deadline(fast_backoff):
    store = FailsFast(failures=2)
    journal = RunJournal("r1", "t1", store, flush_threshold=100, write_timeout=1)
    _emit(journal, 3)
    await journal.flush()
    assert await _contents(store) == ["m0", "m1", "m2"]


async def test_a_timed_out_write_is_not_resent(caplog):
    """The write may have committed before the timeout fired; sending it
    again would store every event twice under new seq numbers."""
    store = CommitsThenHangs()
    journal = RunJournal("r1", "t1", store, flush_threshold=2, write_timeout=0.1)

    with caplog.at_level(logging.WARNING, logger="deerflow.runtime.journal"):
        _emit(journal, 2)
        await asyncio.sleep(0.3)
        journal._cancel_retry()
        journal._retry_not_before = 0.0
        _emit(journal, 2, start=2)
        await journal.flush()

    assert await _contents(store) == ["m0", "m1", "m2", "m3"]
    assert any("timed out" in r.getMessage() and "not re-sent" in r.getMessage() for r in caplog.records)


async def test_a_heartbeat_is_flushed_immediately(monkeypatch):
    store = MemoryRunEventStore()
    journal = RunJournal("r1", "t1", store, flush_threshold=100, write_timeout=1)
    journal._last_heartbeat_mono -= journal_module.HEARTBEAT_INTERVAL_SECONDS + 1

    journal._put(event_type="llm.tool.result", category="message", content="tool done")
    await asyncio.sleep(0.05)

    events = await store.list_events("t1", "r1")
    assert [e["event_type"] for e in events] == ["llm.tool.result", "run.heartbeat"]
    assert journal._buffer == []


async def test_the_buffer_is_capped_while_the_store_is_down(caplog):
    store = FailsFast()
    journal = RunJournal("r1", "t1", store, flush_threshold=100, write_timeout=1, max_buffer_events=10)

    journal._put(event_type="run.start", category="trace")
    with caplog.at_level(logging.WARNING, logger="deerflow.runtime.journal"):
        _emit(journal, 30)
    journal._put(event_type="run.end", category="outputs")

    assert len(journal._buffer) == 10
    kinds = [e["event_type"] for e in journal._buffer]
    # Run boundaries are kept; the oldest ordinary events go first.
    assert kinds[0] == "run.start" and kinds[-1] == "run.end"
    assert journal._buffer[1]["content"] == "m22"
    assert journal._dropped_events == 22
    assert any("buffer full" in r.getMessage() for r in caplog.records)
