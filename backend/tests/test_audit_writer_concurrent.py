"""Regression tests for admin_audit write contention.

Pins the fix for the 2026-08-19 SQLite 'database is locked' storm:
~300+ OperationalError: database is locked tracebacks in logs/gateway.log
from concurrent ops-console requests INSERTing into admin_audit.

The fix has two layers (both verified here):

  1. The async SQLite engine raises busy_timeout to 30 s (engine.py
     connect_args={'timeout': 30} + PRAGMA busy_timeout=30000 in the
     connect listener) so the *common* case of two concurrent writers
     resolves cleanly via the lock-wait, not by exception.

  2. record_audit in app/gateway/admin_ops.py routes through
     _commit_with_retry, a 3-attempt exponential-backoff retry that
     handles the rare case where the 30 s wait was not enough.

Both layers must be in place. Removing either one should fail this test.
"""

from __future__ import annotations

import asyncio
import os
import uuid

import pytest
import pytest_asyncio

os.environ.setdefault("AUTH_JWT_SECRET", "test-secret-key-audit-concurrency-32chars")


@pytest_asyncio.fixture
async def audit_engine(tmp_path):
    """Stand up a real SQLite engine with the production-like settings."""
    from deerflow.persistence.engine import close_engine, init_engine

    url = f"sqlite+aiosqlite:///{tmp_path}/audit_concurrency.db"
    await init_engine("sqlite", url=url, sqlite_dir=str(tmp_path))
    try:
        yield
    finally:
        await close_engine()


@pytest.mark.asyncio
async def test_concurrent_record_audit_does_not_lose_rows(audit_engine):
    """N concurrent record_audit calls land exactly N rows; none raise."""
    from sqlalchemy import func, select

    from app.gateway.admin_ops import record_audit
    from deerflow.persistence.admin_audit.model import AdminAuditRow
    from deerflow.persistence.engine import get_session_factory

    n = 32
    coros = [
        record_audit(
            actor="ops-console",
            action="view-credit-requests",
            target_user_id=None,
            payload={"status_filter": "pending", "limit": 1, "offset": 0, "i": i},
        )
        for i in range(n)
    ]
    # gather with return_exceptions so a regression (one raised) is surfaced
    # instead of being swallowed by the asyncio.run loop.
    results = await asyncio.gather(*coros, return_exceptions=True)
    errors = [r for r in results if isinstance(r, BaseException)]
    assert errors == [], f"{len(errors)} record_audit calls raised: {errors[:3]}"

    # Verify all N rows landed.
    sf = get_session_factory()
    assert sf is not None
    async with sf() as session:
        count = int(await session.scalar(select(func.count()).select_from(AdminAuditRow)) or 0)
    assert count == n, f"expected {n} audit rows, found {count}"


@pytest.mark.asyncio
async def test_commit_with_retry_recovers_from_locked_error(audit_engine, monkeypatch):
    """_commit_with_retry retries on 'database is locked' and eventually succeeds."""
    from sqlalchemy.exc import OperationalError

    from app.gateway import admin_ops

    real_commit = admin_ops.AsyncSession.commit  # type: ignore[attr-defined]

    call_count = {"n": 0}

    async def flaky_commit(self, *args, **kwargs):
        call_count["n"] += 1
        if call_count["n"] <= 2:
            # Mimic the live failure mode: 'database is locked'.
            raise OperationalError("INSERT INTO admin_audit ...", {}, Exception("database is locked"))
        return await real_commit(self, *args, **kwargs)

    monkeypatch.setattr(admin_ops.AsyncSession, "commit", flaky_commit)

    await admin_ops.record_audit(
        actor="ops-console",
        action="flaky-commit-test",
        target_user_id=None,
        payload={"i": uuid.uuid4().hex},
    )
    # Two failures, then the real commit on the third attempt.
    assert call_count["n"] == 3, f"expected 3 attempts, got {call_count['n']}"


@pytest.mark.asyncio
async def test_commit_with_retry_propagates_non_locked_error(audit_engine, monkeypatch):
    """_commit_with_retry must NOT swallow real bugs (schema mismatch, etc.)."""
    from sqlalchemy.exc import OperationalError

    from app.gateway import admin_ops

    async def always_fail(self, *args, **kwargs):
        raise OperationalError("INSERT ...", {}, Exception("no such column: foo.bar"))

    monkeypatch.setattr(admin_ops.AsyncSession, "commit", always_fail)

    with pytest.raises(OperationalError, match="no such column"):
        await admin_ops.record_audit(
            actor="ops-console",
            action="non-lock-failure-test",
            target_user_id=None,
            payload={"i": uuid.uuid4().hex},
        )


@pytest.mark.asyncio
async def test_engine_has_30s_busy_timeout(audit_engine):
    """Pins the engine-level timeout setting that buys time for the retry loop."""
    from sqlalchemy import text

    from deerflow.persistence.engine import get_engine

    eng = get_engine()
    assert eng is not None
    async with eng.connect() as conn:
        # PRAGMA busy_timeout reports the current value in milliseconds.
        result = await conn.execute(text("PRAGMA busy_timeout"))
        row = result.first()
        assert row is not None
        assert int(row[0]) >= 30000, f"busy_timeout too low: {row[0]} ms"

        # WAL must still be on — engine.py keep both settings per connection.
        result = await conn.execute(text("PRAGMA journal_mode"))
        row = result.first()
        assert row is not None
        assert str(row[0]).lower() == "wal", f"journal_mode not WAL: {row[0]}"
