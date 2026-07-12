"""Regression tests for Phase 6 — store-only run cancel + startup reaper.

The plan: when a run has been hydrated from the backing store (worker restart
since the run started) the in-memory ``_runs`` dict has no entry. The previous
``cancel()`` returned False for unknown runs, the router then 409'd with
"not active on this worker", and the user Stop button never landed — the
run row stayed ``running`` forever and the front-end composer never recovered.

Phase 6 fix: ``cancel()`` recognises store-only records (those with status
``pending`` or ``running`` and present in the store) and marks them
``interrupted`` directly through the store, returning True. The worker's
in-memory state stays store_only; the next ``useActiveRun`` poll sees the
new status.

Startup reaper: when the gateway boots, any store row still in
``pending``/``running`` state is necessarily orphaned (the in-memory bridge
dies with the process). The reaper marks them ``interrupted`` so the
front-end polls converge to idle.
"""

from __future__ import annotations

import asyncio

import pytest

from deerflow.runtime.runs.manager import RunManager, RunStatus
from deerflow.runtime.runs.store.memory import MemoryRunStore


def _now() -> str:
    from datetime import UTC, datetime

    return datetime.now(UTC).isoformat()


@pytest.mark.anyio
async def test_cancel_store_only_running_marks_interrupted_and_returns_true():
    """Phase 6 — cancel of a hydrated store-only running row persists 'interrupted'."""
    store = MemoryRunStore()
    mgr = RunManager(store=store)

    # Seed a store row as if a previous worker created the run.
    await store.put(
        "run-store-only-1",
        thread_id="thread-x",
        assistant_id="lead_agent",
        status=RunStatus.running.value,
        created_at=_now(),
    )

    # Sanity: the row exists in the store.
    row = await store.get("run-store-only-1")
    assert row is not None
    assert row["status"] == RunStatus.running.value

    # The in-memory manager hydrates from store, but the record is
    # ``store_only=True`` — no asyncio task, no abort event.
    hydrated = await mgr.get("run-store-only-1")
    assert hydrated is not None
    assert hydrated.store_only is True
    assert hydrated.task is None

    # Phase 6 fix: cancel returns True and persists 'interrupted'.
    result = await mgr.cancel("run-store-only-1", action="interrupt")
    assert result is True, "cancel must return True for store-only running rows"

    row = await store.get("run-store-only-1")
    assert row is not None
    assert row["status"] == RunStatus.interrupted.value


@pytest.mark.anyio
async def test_cancel_store_only_pending_also_returns_true():
    """Same as above but for a pending (never-started) row."""
    store = MemoryRunStore()
    mgr = RunManager(store=store)
    await store.put(
        "run-pending-store",
        thread_id="thread-x",
        status=RunStatus.pending.value,
        created_at=_now(),
    )
    result = await mgr.cancel("run-pending-store")
    assert result is True
    row = await store.get("run-pending-store")
    assert row is not None
    assert row["status"] == RunStatus.interrupted.value


@pytest.mark.anyio
async def test_cancel_store_only_already_terminal_returns_false():
    """A terminal run (success/error/timeout) cannot be cancelled — return False."""
    store = MemoryRunStore()
    mgr = RunManager(store=store)
    await store.put(
        "run-already-done",
        thread_id="thread-x",
        status=RunStatus.success.value,
        created_at=_now(),
    )
    result = await mgr.cancel("run-already-done")
    assert result is False
    row = await store.get("run-already-done")
    assert row["status"] == RunStatus.success.value  # unchanged


@pytest.mark.anyio
async def test_cancel_unknown_run_returns_false():
    """A run with no in-memory record AND no store row must still return False."""
    store = MemoryRunStore()
    mgr = RunManager(store=store)
    result = await mgr.cancel("never-existed")
    assert result is False


@pytest.mark.anyio
async def test_reap_orphaned_runs_marks_active_rows_interrupted():
    """Startup reaper marks all non-terminal store rows interrupted."""
    store = MemoryRunStore()
    mgr = RunManager(store=store)

    # Mix of terminal and active rows.
    await store.put(
        "run-orphan-1",
        thread_id="thread-x",
        status=RunStatus.running.value,
        created_at=_now(),
    )
    await store.put(
        "run-orphan-2",
        thread_id="thread-y",
        status=RunStatus.pending.value,
        created_at=_now(),
    )
    await store.put(
        "run-ok-1",
        thread_id="thread-z",
        status=RunStatus.success.value,
        created_at=_now(),
    )
    await store.put(
        "run-done-1",
        thread_id="thread-w",
        status=RunStatus.error.value,
        created_at=_now(),
    )

    reaped = await mgr.reap_orphaned_runs_for_threads(
        ["thread-x", "thread-y", "thread-z", "thread-w"],
    )

    assert reaped == 2  # only the active rows
    assert (await store.get("run-orphan-1"))["status"] == RunStatus.interrupted.value
    assert (await store.get("run-orphan-2"))["status"] == RunStatus.interrupted.value
    assert (await store.get("run-ok-1"))["status"] == RunStatus.success.value  # untouched
    assert (await store.get("run-done-1"))["status"] == RunStatus.error.value  # untouched


@pytest.mark.anyio
async def test_reap_orphaned_runs_is_idempotent():
    """Re-running the reaper on already-reaped rows is a no-op."""
    store = MemoryRunStore()
    mgr = RunManager(store=store)
    await store.put(
        "run-orphan-1",
        thread_id="thread-x",
        status=RunStatus.running.value,
        created_at=_now(),
    )

    first = await mgr.reap_orphaned_runs_for_threads(["thread-x"])
    second = await mgr.reap_orphaned_runs_for_threads(["thread-x"])

    assert first == 1
    assert second == 0  # already interrupted, nothing left to do
