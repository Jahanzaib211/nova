"""ThreadMetaRepository SQL tests against an in-memory SQLite engine.

Covers:
- create + get + delete round-trip
- metadata round-trip (JSON column remap)
- check_access (permissive vs strict modes)
- search (user + status + metadata filters)
- update_display_name / update_status / update_metadata
- update_owner (transfer)
- Owner isolation: user A can't see/modify user B's thread
"""

from __future__ import annotations

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from deerflow.persistence.base import Base
from deerflow.persistence.thread_meta.sql import ThreadMetaRepository


@pytest.fixture
async def repo():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    sf = async_sessionmaker(engine, expire_on_commit=False)
    yield ThreadMetaRepository(sf)
    await engine.dispose()


class TestThreadMetaRepositoryCRUD:
    @pytest.mark.anyio
    async def test_create_then_get_round_trips(self, repo):
        await repo.create(
            thread_id="t1",
            assistant_id="lead_agent",
            display_name="My Chat",
            metadata={"tags": ["research", "urgent"]},
            user_id="alice",
        )
        row = await repo.get("t1", user_id="alice")
        assert row is not None
        assert row["thread_id"] == "t1"
        assert row["assistant_id"] == "lead_agent"
        assert row["display_name"] == "My Chat"
        assert row["metadata"] == {"tags": ["research", "urgent"]}
        assert row["user_id"] == "alice"

    @pytest.mark.anyio
    async def test_get_missing_returns_none(self, repo):
        assert await repo.get("nonexistent", user_id="alice") is None

    @pytest.mark.anyio
    async def test_owner_isolation_in_get(self, repo):
        await repo.create(thread_id="t_alice", user_id="alice")
        # Bob can't see Alice's thread.
        assert await repo.get("t_alice", user_id="bob") is None
        # Alice can see her own.
        assert await repo.get("t_alice", user_id="alice") is not None

    @pytest.mark.anyio
    async def test_delete_removes_row(self, repo):
        await repo.create(thread_id="t1", user_id="alice")
        await repo.delete("t1", user_id="alice")
        assert await repo.get("t1", user_id="alice") is None

    @pytest.mark.anyio
    async def test_delete_owner_mismatch_is_noop(self, repo):
        await repo.create(thread_id="t1", user_id="alice")
        await repo.delete("t1", user_id="bob")
        # Row still exists for the real owner.
        assert await repo.get("t1", user_id="alice") is not None


class TestThreadMetaRepositoryCheckAccess:
    @pytest.mark.anyio
    async def test_permissive_mode_returns_true_for_missing(self, repo):
        """Permissive mode (default): untracked threads are accessible (backward-compat)."""
        assert await repo.check_access("nonexistent", "alice", require_existing=False) is True

    @pytest.mark.anyio
    async def test_strict_mode_returns_false_for_missing(self, repo):
        """Strict mode: untracked threads are NOT accessible for mutating ops."""
        assert await repo.check_access("nonexistent", "alice", require_existing=True) is False

    @pytest.mark.anyio
    async def test_permissive_owner_match(self, repo):
        await repo.create(thread_id="t1", user_id="alice")
        assert await repo.check_access("t1", "alice", require_existing=False) is True

    @pytest.mark.anyio
    async def test_permissive_owner_mismatch(self, repo):
        await repo.create(thread_id="t1", user_id="alice")
        # Bob is NOT the owner → access denied in both modes.
        assert await repo.check_access("t1", "bob") is False
        assert await repo.check_access("t1", "bob", require_existing=True) is False

    @pytest.mark.anyio
    async def test_strict_after_delete(self, repo):
        """The delete-idempotence cross-user gap fix: after Alice deletes,
        Bob cannot target the thread even though no row exists."""
        await repo.create(thread_id="t1", user_id="alice")
        await repo.delete("t1", user_id="alice")
        # Strict mode: untracked thread NOT accessible to anyone.
        assert await repo.check_access("t1", "bob", require_existing=True) is False


class TestThreadMetaRepositorySearch:
    @pytest.mark.anyio
    async def test_search_returns_users_threads_only(self, repo):
        await repo.create(thread_id="t_alice_1", user_id="alice", metadata={"topic": "research"})
        await repo.create(thread_id="t_alice_2", user_id="alice", metadata={"topic": "code"})
        await repo.create(thread_id="t_bob_1", user_id="bob", metadata={"topic": "research"})

        alice_results = await repo.search(user_id="alice")
        assert {r["thread_id"] for r in alice_results} == {"t_alice_1", "t_alice_2"}

    @pytest.mark.anyio
    async def test_search_by_status(self, repo):
        await repo.create(thread_id="t_idle", user_id="alice")
        await repo.create(thread_id="t_done", user_id="alice")
        await repo.update_status("t_done", "completed", user_id="alice")

        idle_results = await repo.search(status="idle", user_id="alice")
        assert {r["thread_id"] for r in idle_results} == {"t_idle"}

    @pytest.mark.anyio
    async def test_search_by_metadata(self, repo):
        # json_match only supports scalar values (str, int, bool, float, None)
        # not list membership. So store a scalar priority field.
        await repo.create(thread_id="t_urgent", user_id="alice", metadata={"priority": "urgent"})
        await repo.create(thread_id="t_low", user_id="alice", metadata={"priority": "low"})

        urgent = await repo.search(metadata={"priority": "urgent"}, user_id="alice")
        assert {r["thread_id"] for r in urgent} == {"t_urgent"}

    @pytest.mark.anyio
    async def test_search_respects_limit_and_offset(self, repo):
        for i in range(5):
            await repo.create(thread_id=f"t{i}", user_id="alice")
        first_two = await repo.search(user_id="alice", limit=2, offset=0)
        assert len(first_two) == 2


class TestThreadMetaRepositoryUpdates:
    @pytest.mark.anyio
    async def test_update_display_name(self, repo):
        await repo.create(thread_id="t1", user_id="alice", display_name="Original")
        await repo.update_display_name("t1", "Updated", user_id="alice")
        row = await repo.get("t1", user_id="alice")
        assert row["display_name"] == "Updated"

    @pytest.mark.anyio
    async def test_update_status(self, repo):
        await repo.create(thread_id="t1", user_id="alice")
        await repo.update_status("t1", "running", user_id="alice")
        row = await repo.get("t1", user_id="alice")
        assert row["status"] == "running"

    @pytest.mark.anyio
    async def test_update_metadata_merges(self, repo):
        await repo.create(thread_id="t1", user_id="alice", metadata={"a": 1, "b": 2})
        await repo.update_metadata("t1", {"b": 99, "c": 3}, user_id="alice")
        row = await repo.get("t1", user_id="alice")
        # Existing keys updated, new keys added, untouched keys preserved.
        assert row["metadata"] == {"a": 1, "b": 99, "c": 3}

    @pytest.mark.anyio
    async def test_update_owner_transfers_thread(self, repo):
        await repo.create(thread_id="t1", user_id="alice")
        await repo.update_owner("t1", "bob", user_id="alice")
        # Bob now owns it; Alice can't see it.
        assert await repo.get("t1", user_id="bob") is not None
        assert await repo.get("t1", user_id="alice") is None

    @pytest.mark.anyio
    async def test_update_owner_owner_mismatch_is_noop(self, repo):
        await repo.create(thread_id="t1", user_id="alice")
        await repo.update_owner("t1", "bob", user_id="bob")
        # Bob tried to transfer Alice's thread to himself → no change.
        row = await repo.get("t1", user_id="alice")
        assert row is not None


class TestThreadMetaRepositoryDelete:
    @pytest.mark.anyio
    async def test_delete_returns_silently_when_row_missing(self, repo):
        await repo.delete("nonexistent", user_id="alice")  # must not raise
