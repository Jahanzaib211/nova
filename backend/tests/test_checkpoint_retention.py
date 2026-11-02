"""Checkpoint retention on SQLite, against checkpoints written by LangGraph's real saver.

The rows are produced by ``AsyncSqliteSaver`` driving a real compiled graph, so
the schema and the ``checkpoint_id`` format are LangGraph's own, not a guess.
The Postgres SQL is pinned against LangGraph's Postgres migrations in
``tests/test_checkpoint_retention_pg_schema.py``.
"""

from __future__ import annotations

import operator
import time
from typing import Annotated, TypedDict

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

from nova.persistence.checkpoint_retention import RetentionPolicy, prune_checkpoints, uuid6_for_epoch, uuid6_unix_seconds

pytestmark = pytest.mark.anyio

pytest.importorskip("langgraph.checkpoint.sqlite.aio")


@pytest.fixture
def anyio_backend():
    return "asyncio"


class _State(TypedDict):
    items: Annotated[list[int], operator.add]


async def _write_threads(db_path: str, steps_by_thread: dict[str, int]) -> None:
    """Run a one-node graph ``steps`` times per thread -> real checkpoints + writes."""
    from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver
    from langgraph.graph import END, START, StateGraph

    builder = StateGraph(_State)
    builder.add_node("step", lambda state: {"items": [len(state.get("items") or [])]})
    builder.add_edge(START, "step")
    builder.add_edge("step", END)

    async with AsyncSqliteSaver.from_conn_string(db_path) as saver:
        graph = builder.compile(checkpointer=saver)
        for thread_id, steps in steps_by_thread.items():
            for _ in range(steps):
                await graph.ainvoke({"items": []}, {"configurable": {"thread_id": thread_id}})


async def _latest_state(db_path: str, thread_id: str):
    from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver

    async with AsyncSqliteSaver.from_conn_string(db_path) as saver:
        tup = await saver.aget_tuple({"configurable": {"thread_id": thread_id}})
        return tup.checkpoint["channel_values"] if tup else None


async def _count(engine, sql: str, **params) -> int:
    async with engine.connect() as conn:
        return int((await conn.execute(text(sql), params)).scalar_one())


@pytest.fixture
async def db(tmp_path):
    path = str(tmp_path / "ckpt.db")
    await _write_threads(path, {"busy": 6, "idle": 6, "small": 2})
    engine = create_async_engine(f"sqlite+aiosqlite:///{path}")
    async with engine.begin() as conn:
        await conn.execute(text("CREATE TABLE runs (run_id TEXT PRIMARY KEY, thread_id TEXT, status TEXT)"))
    yield path, engine
    await engine.dispose()


def test_cutoff_orders_against_langgraph_checkpoint_ids():
    from langgraph.checkpoint.base.id import uuid6

    now = time.time()
    real = str(uuid6())
    assert abs(uuid6_unix_seconds(real) - now) < 5
    # String order is time order: what the activity window relies on.
    assert uuid6_for_epoch(now - 60) < real < uuid6_for_epoch(now + 60)
    assert uuid6_unix_seconds("not-a-uuid") is None


async def test_dry_run_is_the_default_and_deletes_nothing(db):
    path, engine = db
    before = await _count(engine, "SELECT COUNT(*) FROM checkpoints")

    report = await prune_checkpoints(engine, RetentionPolicy(keep_per_thread=2, keep_active_days=0))

    assert report.dry_run is True
    assert report.doomed_checkpoints > 0
    assert report.reclaimable_bytes > 0
    assert report.deleted_checkpoints == 0
    assert await _count(engine, "SELECT COUNT(*) FROM checkpoints") == before


async def test_keeps_newest_n_per_thread_and_deletes_writes_consistently(db):
    path, engine = db
    state_before = await _latest_state(path, "idle")
    newest_before = await _count(engine, "SELECT COUNT(*) FROM checkpoints WHERE thread_id = 'idle'")
    assert newest_before > 2

    report = await prune_checkpoints(engine, RetentionPolicy(keep_per_thread=2, keep_active_days=0, batch_size=3), dry_run=False)

    assert report.complete
    assert report.batches >= 2, "batch_size must bound each transaction"
    for thread in ("busy", "idle", "small"):
        assert await _count(engine, "SELECT COUNT(*) FROM checkpoints WHERE thread_id = :t", t=thread) == 2
    # No write survives its checkpoint.
    orphans = await _count(
        engine,
        "SELECT COUNT(*) FROM writes w WHERE NOT EXISTS (SELECT 1 FROM checkpoints c WHERE c.thread_id = w.thread_id AND c.checkpoint_ns = w.checkpoint_ns AND c.checkpoint_id = w.checkpoint_id)",
    )
    assert orphans == 0
    assert report.deleted_checkpoints == report.doomed_checkpoints
    # The thread still resumes from exactly the state it had.
    assert await _latest_state(path, "idle") == state_before


async def test_a_thread_with_a_running_run_is_never_touched(db):
    path, engine = db
    async with engine.begin() as conn:
        await conn.execute(text("INSERT INTO runs VALUES ('r1', 'busy', 'running'), ('r2', 'idle', 'success')"))
    busy_before = await _count(engine, "SELECT COUNT(*) FROM checkpoints WHERE thread_id = 'busy'")

    report = await prune_checkpoints(engine, RetentionPolicy(keep_per_thread=1, keep_active_days=0), dry_run=False)

    assert await _count(engine, "SELECT COUNT(*) FROM checkpoints WHERE thread_id = 'busy'") == busy_before
    assert await _count(engine, "SELECT COUNT(*) FROM checkpoints WHERE thread_id = 'idle'") == 1
    assert report.protected_threads == 1


async def test_recently_active_threads_are_kept_whole(db):
    path, engine = db
    before = await _count(engine, "SELECT COUNT(*) FROM checkpoints")

    # Everything was written seconds ago, so a 7-day window protects it all.
    report = await prune_checkpoints(engine, RetentionPolicy(keep_per_thread=1, keep_active_days=7), dry_run=False)

    assert report.protected_threads == 3
    assert report.doomed_checkpoints == 0
    assert await _count(engine, "SELECT COUNT(*) FROM checkpoints") == before

    # ...and the same threads become eligible once the window has passed.
    report = await prune_checkpoints(engine, RetentionPolicy(keep_per_thread=1, keep_active_days=7), dry_run=True, now=time.time() + 8 * 86400)
    assert report.protected_threads == 0
    assert report.doomed_checkpoints > 0


async def test_max_batches_bounds_a_sweep_and_the_next_one_continues(db):
    path, engine = db
    policy = RetentionPolicy(keep_per_thread=1, keep_active_days=0, batch_size=2, max_batches=1)

    first = await prune_checkpoints(engine, policy, dry_run=False)
    assert first.deleted_checkpoints == 2
    assert first.complete is False

    while not (await prune_checkpoints(engine, policy, dry_run=False)).complete:
        pass
    for thread in ("busy", "idle", "small"):
        assert await _count(engine, "SELECT COUNT(*) FROM checkpoints WHERE thread_id = :t", t=thread) == 1


async def test_missing_tables_are_a_no_op(tmp_path):
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'empty.db'}")
    try:
        report = await prune_checkpoints(engine, RetentionPolicy(), dry_run=False)
    finally:
        await engine.dispose()
    assert report.doomed_checkpoints == 0
    assert report.notes


def test_policy_refuses_to_keep_nothing():
    with pytest.raises(ValueError):
        RetentionPolicy(keep_per_thread=0)
