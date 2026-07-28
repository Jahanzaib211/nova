"""Tests for Phase C0.1 — cross-process correlation_id on RunRecord.

Verifies that:
- ``correlation_id`` is generated at create() / create_or_reject() time
- It is unique per run (independent of run_id)
- It is persisted through the store (round-trip on _record_from_store)
- It defaults to "" for legacy rows (backward compat)

These tests are the foundation for end-to-end trace correlation across
backend, SSE, and frontend. They MUST NOT require any other module's
behaviour — pure RunManager / RunStore contract.
"""

from __future__ import annotations

import asyncio

import pytest

from deerflow.runtime.runs.manager import RunManager, RunStatus
from deerflow.runtime.runs.store.memory import MemoryRunStore


@pytest.mark.anyio
async def test_create_generates_nonempty_correlation_id():
    mgr = RunManager()
    record = await mgr.create("thread-a", assistant_id="lead_agent")
    assert record.correlation_id, "create() must populate correlation_id"
    assert len(record.correlation_id) >= 16, "correlation_id must be long enough to be a UUID hex"


@pytest.mark.anyio
async def test_create_generates_unique_correlation_ids():
    mgr = RunManager()
    r1 = await mgr.create("thread-a")
    r2 = await mgr.create("thread-b")
    assert r1.correlation_id != r2.correlation_id, "correlation_id must be unique per run"


@pytest.mark.anyio
async def test_create_or_reject_generates_correlation_id():
    mgr = RunManager()
    record = await mgr.create_or_reject("thread-a")
    assert record.correlation_id
    assert len(record.correlation_id) >= 16


@pytest.mark.anyio
async def test_correlation_id_is_independent_of_run_id():
    """correlation_id must NOT be derived from run_id — the directive mandates
    a separate identifier so the platform can correlate across multiple
    LangGraph runs (e.g. parent + subagent) sharing one platform-level session.
    """
    mgr = RunManager()
    record = await mgr.create("thread-a")
    assert record.run_id != record.correlation_id, "correlation_id must be a distinct identifier, not equal to run_id"


@pytest.mark.anyio
async def test_correlation_id_round_trips_through_store():
    store = MemoryRunStore()
    mgr = RunManager(store=store)
    record = await mgr.create("thread-a")
    assert record.correlation_id

    # Re-fetch via store hydration (this is the path the gateway uses after
    # worker restart — must preserve the correlation_id).
    row = await store.get(record.run_id)
    assert row is not None
    assert row.get("correlation_id") == record.correlation_id

    hydrated = await mgr.get(record.run_id)
    assert hydrated is not None
    assert hydrated.correlation_id == record.correlation_id


@pytest.mark.anyio
async def test_correlation_id_survives_create_or_reject_replacement():
    """When create_or_reject cancels the previous run and creates a new one,
    the NEW run gets its own correlation_id; the cancelled one is unchanged.
    """
    mgr = RunManager()
    first = await mgr.create_or_reject("thread-a")
    second = await mgr.create_or_reject("thread-a", multitask_strategy="interrupt")
    assert second.correlation_id != first.correlation_id
    assert first.correlation_id  # unchanged


@pytest.mark.anyio
async def test_legacy_store_row_without_correlation_id_hydrates_with_empty_string():
    """Backward compatibility: rows written before Phase C0 have no
    correlation_id column. Hydration must default to "" (empty string) so
    consumers can fall back to run_id.
    """
    store = MemoryRunStore()
    # Insert a legacy row directly into the store, bypassing create().
    await store.put(
        "legacy-run-1",
        thread_id="thread-a",
        status=RunStatus.success.value,
        # NOTE: no correlation_id kwarg
    )
    mgr = RunManager(store=store)
    hydrated = await mgr.get("legacy-run-1")
    assert hydrated is not None
    assert hydrated.correlation_id == "", "legacy rows must hydrate with empty correlation_id"
