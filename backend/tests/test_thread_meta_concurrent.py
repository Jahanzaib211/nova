"""Regression test for concurrent thread_meta writes.

Pins the same fix as test_audit_writer_concurrent.py — the
``commit_with_lock_retry`` helper from
``deerflow.persistence.engine`` is wired into every
``ThreadMetaRepository`` write site (``create``, ``update_display_name``,
``update_status``, ``update_metadata``, ``update_owner``, ``delete``).

Without the fix, the 2026-08-20 production outage had
``POST /api/threads`` returning HTTP 500 ``{"detail":"Failed to create
thread"}`` because two concurrent thread-creation requests (e.g. one
creating, one writing title metadata) raced for the SQLite write lock and
the loser raised before ``busy_timeout`` elapsed.
"""

from __future__ import annotations

import asyncio
import os
import uuid

import pytest
import pytest_asyncio

os.environ.setdefault("AUTH_JWT_SECRET", "test-secret-key-thread-meta-concurrency-32")


@pytest_asyncio.fixture
async def thread_engine(tmp_path):
    from deerflow.persistence.engine import close_engine, init_engine
    from deerflow.persistence.models import ThreadMetaRow  # noqa: F401 — register model

    url = f"sqlite+aiosqlite:///{tmp_path}/thread_meta_concurrency.db"
    await init_engine("sqlite", url=url, sqlite_dir=str(tmp_path))
    try:
        yield
    finally:
        await close_engine()


@pytest.mark.asyncio
async def test_concurrent_thread_create_does_not_lose_rows(thread_engine):
    from sqlalchemy import func, select

    from deerflow.persistence.engine import get_session_factory
    from deerflow.persistence.models import ThreadMetaRow
    from deerflow.persistence.thread_meta.sql import ThreadMetaRepository

    repo = ThreadMetaRepository(get_session_factory())
    n = 32
    ids = [uuid.uuid4().hex for _ in range(n)]
    coros = [repo.create(thread_id=tid, display_name=f"thread {i}") for i, tid in enumerate(ids)]
    results = await asyncio.gather(*coros, return_exceptions=True)
    errors = [r for r in results if isinstance(r, BaseException)]
    assert errors == [], f"{len(errors)} repo.create calls raised: {errors[:3]}"

    # Verify all N rows landed.
    sf = get_session_factory()
    async with sf() as session:
        count = int(await session.scalar(select(func.count()).select_from(ThreadMetaRow)) or 0)
    assert count == n, f"expected {n} thread rows, found {count}"


@pytest.mark.asyncio
async def test_concurrent_create_and_update_does_not_lose(thread_engine):
    """Real production shape: one caller creates a thread while another
    updates its status / display_name in parallel. Without the retry, the
    status updater loses the race and the user sees an HTTP 500."""
    from sqlalchemy import func, select

    from deerflow.persistence.engine import get_session_factory
    from deerflow.persistence.models import ThreadMetaRow
    from deerflow.persistence.thread_meta.sql import ThreadMetaRepository

    repo = ThreadMetaRepository(get_session_factory())
    # Create one thread first.
    tid = uuid.uuid4().hex
    await repo.create(thread_id=tid)

    # Now race updates against another batch of creates on other ids.
    other_ids = [uuid.uuid4().hex for _ in range(16)]
    create_coros = [repo.create(thread_id=t) for t in other_ids]
    update_coros = [repo.update_status(tid, status) for status in ("running", "completed", "failed") * 5]
    results = await asyncio.gather(*create_coros, *update_coros, return_exceptions=True)
    errors = [r for r in results if isinstance(r, BaseException)]
    assert errors == [], f"{len(errors)} concurrent ops raised: {errors[:3]}"

    # Final state: 17 rows (1 + 16), tid at the last status.
    sf = get_session_factory()
    async with sf() as session:
        count = int(await session.scalar(select(func.count()).select_from(ThreadMetaRow)) or 0)
        row = await session.get(ThreadMetaRow, tid)
    assert count == 17, f"expected 17 rows, found {count}"
    assert row.status in {"running", "completed", "failed"}


@pytest.mark.asyncio
async def test_create_survives_rollback_with_on_retry(thread_engine, monkeypatch):
    """Regression for the v9.4 second-wind bug: ThreadMetaRepository.create()
    used to pass ``await commit_with_lock_retry(session)`` directly, so a
    lock-induced rollback expunged the row from the session and the
    subsequent ``session.refresh(row)`` raised
    ``Instance … is not persistent within this Session`` — turning every
    locked write into an HTTP 500.

    The fix wires an ``on_retry`` callback that re-adds the row after each
    rollback, so the post-commit ``session.refresh(row)`` always sees a
    persistent instance.

    Pinning the contract: after two forced rollback attempts the row is
    still present in the DB, the create returns a valid dict, and the
    on_retry callback was invoked exactly twice.
    """
    from sqlalchemy import func, select
    from sqlalchemy.exc import OperationalError
    from sqlalchemy.ext.asyncio import AsyncSession

    from deerflow.persistence import thread_meta as tm
    from deerflow.persistence.engine import get_session_factory
    from deerflow.persistence.models import ThreadMetaRow

    sf = get_session_factory()
    repo = tm.sql.ThreadMetaRepository(sf)

    call_count = {"n": 0}
    real_commit = AsyncSession.commit

    async def flaky_commit(self, *args, **kwargs):
        call_count["n"] += 1
        if call_count["n"] <= 2:
            raise OperationalError("INSERT INTO thread_meta ...", {}, Exception("database is locked"))
        return await real_commit(self, *args, **kwargs)

    monkeypatch.setattr(AsyncSession, "commit", flaky_commit)

    tid = uuid.uuid4().hex
    row_dict = await repo.create(thread_id=tid, display_name="rollback test")
    assert row_dict["thread_id"] == tid
    assert row_dict["display_name"] == "rollback test"

    # Exactly 3 commit attempts: 2 fail + 1 success.
    assert call_count["n"] == 3, f"expected 3 commit attempts, got {call_count['n']}"

    # Row actually landed.
    async with sf() as session:
        count = int(await session.scalar(select(func.count()).select_from(ThreadMetaRow)) or 0)
    assert count == 1
