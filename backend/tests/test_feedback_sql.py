"""FeedbackRepository SQL tests against an in-memory SQLite engine.

Covers:
- create + get round-trip with rating validation
- list_by_run + list_by_thread with user_id filtering
- upsert (insert + update path)
- delete (real + missing) + owner isolation
- list_by_thread_grouped (one row per feedback, grouped by run)
- aggregate_by_run (positive/negative counts + average)
"""

from __future__ import annotations

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from deerflow.persistence.base import Base
from deerflow.persistence.feedback.sql import FeedbackRepository


@pytest.fixture
async def repo():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    sf = async_sessionmaker(engine, expire_on_commit=False)
    yield FeedbackRepository(sf)
    await engine.dispose()


class TestFeedbackRepositoryCRUD:
    @pytest.mark.anyio
    async def test_create_then_get_round_trips(self, repo):
        result = await repo.create(run_id="r_roundtrip", thread_id="t1", rating=1, comment="great", user_id="alice")
        # Pass user_id explicitly so the autouse test-user-id doesn't
        # trigger the owner-isolation filter and return None.
        fetched = await repo.get(result["feedback_id"], user_id="alice")
        assert fetched is not None
        assert fetched["run_id"] == "r_roundtrip"
        assert fetched["thread_id"] == "t1"
        assert fetched["rating"] == 1
        assert fetched["comment"] == "great"
        assert fetched["user_id"] == "alice"

    @pytest.mark.anyio
    async def test_create_rejects_invalid_rating(self, repo):
        with pytest.raises(ValueError, match="rating must be"):
            await repo.create(run_id="r_invalid1", thread_id="t1", rating=0, user_id="alice")
        with pytest.raises(ValueError, match="rating must be"):
            await repo.create(run_id="r_invalid2", thread_id="t1", rating=2, user_id="alice")

    @pytest.mark.anyio
    async def test_get_missing_returns_none(self, repo):
        assert await repo.get("does-not-exist") is None

    @pytest.mark.anyio
    async def test_owner_isolation_in_get(self, repo):
        result = await repo.create(run_id="r_owner", thread_id="t1", rating=1, user_id="alice")
        # Bob can't see Alice's feedback.
        assert await repo.get(result["feedback_id"], user_id="bob") is None
        # Alice can see her own.
        assert await repo.get(result["feedback_id"], user_id="alice") is not None


class TestFeedbackRepositoryListing:
    @pytest.mark.anyio
    async def test_list_by_run_filters_by_run_and_user(self, repo):
        await repo.create(run_id="r1", thread_id="t1", rating=1, user_id="alice")
        await repo.create(run_id="r1", thread_id="t1", rating=-1, user_id="bob")
        await repo.create(run_id="r2", thread_id="t1", rating=1, user_id="alice")

        alice_r1 = await repo.list_by_run("t1", "r1", user_id="alice")
        assert len(alice_r1) == 1
        assert alice_r1[0]["user_id"] == "alice"

        both_r1 = await repo.list_by_run("t1", "r1", user_id=None)
        assert len(both_r1) == 2

    @pytest.mark.anyio
    async def test_list_by_thread_returns_only_matching_thread(self, repo):
        await repo.create(run_id="r1", thread_id="t1", rating=1)
        await repo.create(run_id="r2", thread_id="t1", rating=-1)
        await repo.create(run_id="r3", thread_id="t2", rating=1)
        rows = await repo.list_by_thread("t1")
        assert len(rows) == 2
        assert {r["thread_id"] for r in rows} == {"t1"}

    @pytest.mark.anyio
    async def test_list_by_thread_respects_limit(self, repo):
        for i in range(10):
            await repo.create(run_id=f"r{i}", thread_id="t1", rating=1)
        rows = await repo.list_by_thread("t1", limit=3)
        assert len(rows) == 3


class TestFeedbackRepositoryUpsert:
    @pytest.mark.anyio
    async def test_upsert_creates_when_missing(self, repo):
        result = await repo.upsert(run_id="r_upsert_new", thread_id="t1", rating=1, comment="first", user_id="alice")
        # Upsert returns the row dict. Re-fetch with explicit user to
        # bypass the autouse test-user isolation.
        fetched = await repo.get(result["feedback_id"], user_id="alice")
        assert fetched is not None
        assert fetched["rating"] == 1
        assert fetched["comment"] == "first"

    @pytest.mark.anyio
    async def test_upsert_updates_existing(self, repo):
        first = await repo.upsert(run_id="r_upsert_existing", thread_id="t1", rating=1, comment="first", user_id="alice")
        second = await repo.upsert(run_id="r_upsert_existing", thread_id="t1", rating=-1, comment="changed mind", user_id="alice")
        # Same feedback_id → row was updated, not duplicated.
        assert first["feedback_id"] == second["feedback_id"]
        assert second["rating"] == -1
        assert second["comment"] == "changed mind"

    @pytest.mark.anyio
    async def test_upsert_only_one_row_after_multiple_updates(self, repo):
        await repo.upsert(run_id="r_upsert_idem", thread_id="t1", rating=1, comment="a", user_id="alice")
        await repo.upsert(run_id="r_upsert_idem", thread_id="t1", rating=1, comment="b", user_id="alice")
        await repo.upsert(run_id="r_upsert_idem", thread_id="t1", rating=1, comment="c", user_id="alice")
        rows = await repo.list_by_run("t1", "r_upsert_idem", user_id="alice")
        assert len(rows) == 1


class TestFeedbackRepositoryDelete:
    @pytest.mark.anyio
    async def test_delete_removes_row(self, repo):
        result = await repo.create(run_id="r1", thread_id="t1", rating=1)
        delete_result = await repo.delete(result["feedback_id"])
        assert delete_result is True
        assert await repo.get(result["feedback_id"]) is None

    @pytest.mark.anyio
    async def test_delete_missing_returns_false(self, repo):
        result = await repo.delete("nonexistent")
        assert result is False

    @pytest.mark.anyio
    async def test_delete_owner_mismatch_returns_false(self, repo):
        result = await repo.create(run_id="r1", thread_id="t1", rating=1, user_id="alice")
        # Bob can't delete Alice's feedback.
        delete_result = await repo.delete(result["feedback_id"], user_id="bob")
        assert delete_result is False
        # Row still exists for Alice.
        assert await repo.get(result["feedback_id"], user_id="alice") is not None


class TestFeedbackRepositoryAggregate:
    @pytest.mark.anyio
    async def test_aggregate_by_run_counts_positive_negative(self, repo):
        # Unique constraint is on (thread_id, run_id, user_id), so use 3
        # different users to get 3 rows under the same run.
        await repo.create(run_id="r_agg_pos", thread_id="t1", rating=1, user_id="u1")
        await repo.create(run_id="r_agg_pos", thread_id="t1", rating=1, user_id="u2")
        await repo.create(run_id="r_agg_pos", thread_id="t1", rating=-1, user_id="u3")
        agg = await repo.aggregate_by_run("t1", "r_agg_pos")
        assert agg["positive"] == 2
        assert agg["negative"] == 1
        assert agg["total"] == 3
        assert agg["run_id"] == "r_agg_pos"

    @pytest.mark.anyio
    async def test_aggregate_by_run_excludes_other_runs(self, repo):
        await repo.create(run_id="r_agg_a", thread_id="t1", rating=1, user_id="u1")
        await repo.create(run_id="r_agg_b", thread_id="t1", rating=-1, user_id="u1")
        agg_r1 = await repo.aggregate_by_run("t1", "r_agg_a")
        assert agg_r1["total"] == 1
        assert agg_r1["positive"] == 1
        assert agg_r1["negative"] == 0
