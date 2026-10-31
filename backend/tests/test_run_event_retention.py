"""run_events must be bounded, and bounded safely.

After the checkpoint tables were vacuumed (6,084 MB -> 439 MB), run_events was
the largest object left and the only one vacuuming cannot help: 366 MB of which
335 MB is genuine content. It grows with every run and nothing removed a row.

The dangerous failure mode here is not "table too big" -- it is deleting a
user's conversation history that nobody asked to delete. So retention is off
unless configured, the default store implementation prunes nothing, and the
sweep can never take the gateway down.
"""

from __future__ import annotations

import asyncio
import datetime as dt

import pytest

from deerflow.config.run_events_config import RunEventsConfig
from deerflow.runtime.events.retention import (
    prune_once,
    start_run_event_pruner,
    stop_run_event_pruner,
)
from deerflow.runtime.events.store.base import RunEventStore


class TestDefaultsAreSafe:
    def test_retention_is_off_by_default(self) -> None:
        """Silently deleting history on upgrade would be worse than the growth."""
        assert RunEventsConfig().retention_days == 0

    def test_negative_retention_is_rejected(self) -> None:
        with pytest.raises(ValueError):
            RunEventsConfig(retention_days=-1)

    def test_the_base_store_prunes_nothing(self) -> None:
        """delete_older_than is deliberately not abstract: a store that cannot
        prune is not broken, it just keeps everything, as it always did."""
        assert "delete_older_than" not in RunEventStore.__abstractmethods__


class _FakeStore:
    def __init__(self, *, fail: bool = False):
        self.calls: list[dt.datetime] = []
        self.fail = fail

    async def delete_older_than(self, cutoff) -> int:
        self.calls.append(cutoff)
        if self.fail:
            raise RuntimeError("database unavailable")
        return 7


class TestPruneOnce:
    @pytest.mark.asyncio
    async def test_disabled_retention_never_touches_the_store(self) -> None:
        store = _FakeStore()
        assert await prune_once(store, 0) == 0
        assert store.calls == [], "a disabled sweep still issued a delete"

    @pytest.mark.asyncio
    async def test_cutoff_is_retention_days_in_the_past(self) -> None:
        store = _FakeStore()
        before = dt.datetime.now(dt.UTC)
        await prune_once(store, 30)
        cutoff = store.calls[0]
        age = before - cutoff
        assert 29.9 < age.total_seconds() / 86400 < 30.1
        assert cutoff.tzinfo is not None, "a naive cutoff compares wrongly against a tz-aware column"

    @pytest.mark.asyncio
    async def test_returns_the_number_deleted(self) -> None:
        assert await prune_once(_FakeStore(), 30) == 7


class TestTheSweepIsNeverFatal:
    @pytest.mark.asyncio
    async def test_disabled_retention_starts_no_task(self) -> None:
        assert start_run_event_pruner(store=_FakeStore(), retention_days=0) is None

    @pytest.mark.asyncio
    async def test_stopping_a_disabled_pruner_is_safe(self) -> None:
        await stop_run_event_pruner(None)

    @pytest.mark.asyncio
    async def test_a_failing_sweep_does_not_kill_the_task(self) -> None:
        """Retention is maintenance. It must not take the gateway with it."""
        store = _FakeStore(fail=True)
        task = start_run_event_pruner(store=store, retention_days=30)
        assert task is not None
        await asyncio.sleep(0.05)
        assert not task.done(), "one failed sweep ended the retention task"
        assert store.calls, "the sweep never ran"
        await stop_run_event_pruner(task)

    @pytest.mark.asyncio
    async def test_it_sweeps_immediately_rather_than_after_a_full_interval(self) -> None:
        """An hourly sweep that waits an hour first does nothing on a gateway
        that restarts often -- and this one restarts a lot."""
        store = _FakeStore()
        task = start_run_event_pruner(store=store, retention_days=30)
        await asyncio.sleep(0.05)
        assert store.calls, "no sweep before the first interval elapsed"
        await stop_run_event_pruner(task)

    @pytest.mark.asyncio
    async def test_stop_cancels_cleanly(self) -> None:
        task = start_run_event_pruner(store=_FakeStore(), retention_days=30)
        await stop_run_event_pruner(task)
        assert task is not None and task.cancelled() or task.done()
