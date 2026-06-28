"""SQLAlchemy-backed RunRepository tests against an in-memory SQLite engine.

Covers:
- Basic CRUD (put, get, update_status, delete)
- Idempotent put (second put updates existing row, doesn't fail)
- list_by_thread + user_id filtering
- list_pending + list_inflight (FIFO ordering, before filter)
- update_run_completion + update_run_progress
- aggregate_tokens_by_thread (per-model breakdown, JSON column)
- _safe_json normalization (datetime, Pydantic models, unserializable)
- Owner isolation: user A can't see user B's runs
- _row_to_dict JSON column remap (metadata_json → metadata)

Each test creates its own in-memory engine so they're isolated.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from deerflow.persistence.base import Base
from deerflow.persistence.run.sql import RunRepository


@pytest.fixture
async def repo():
    """Spin up an isolated in-memory SQLite + create_all + return a fresh repo."""
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    sf = async_sessionmaker(engine, expire_on_commit=False)
    yield RunRepository(sf)
    await engine.dispose()


class TestRunRepositoryCRUD:
    @pytest.mark.anyio
    async def test_put_then_get_round_trips_all_fields(self, repo):
        await repo.put(
            "r1",
            thread_id="t1",
            assistant_id="lead_agent",
            status="running",
            model_name="minimax-m3",
            metadata={"trigger": "user"},
            kwargs={"input": {"messages": []}},
        )
        row = await repo.get("r1")
        assert row is not None
        assert row["run_id"] == "r1"
        assert row["thread_id"] == "t1"
        assert row["assistant_id"] == "lead_agent"
        assert row["status"] == "running"
        assert row["model_name"] == "minimax-m3"
        assert row["metadata"] == {"trigger": "user"}  # JSON column remapped
        assert row["kwargs"] == {"input": {"messages": []}}

    @pytest.mark.anyio
    async def test_put_is_idempotent(self, repo):
        """A second put with the same run_id updates rather than failing on PK collision."""
        await repo.put("r1", thread_id="t1", status="pending")
        await repo.put("r1", thread_id="t1", status="running")
        row = await repo.get("r1")
        assert row["status"] == "running"
        # Only one row in DB.
        rows = await repo.list_by_thread("t1")
        assert len(rows) == 1

    @pytest.mark.anyio
    async def test_get_missing_returns_none(self, repo):
        assert await repo.get("nonexistent") is None

    @pytest.mark.anyio
    async def test_update_status_returns_true_when_row_exists(self, repo):
        await repo.put("r1", thread_id="t1")
        updated = await repo.update_status("r1", "success")
        assert updated is True
        assert (await repo.get("r1"))["status"] == "success"

    @pytest.mark.anyio
    async def test_update_status_returns_false_for_missing(self, repo):
        assert await repo.update_status("nope", "success") is False

    @pytest.mark.anyio
    async def test_delete_removes_row(self, repo):
        await repo.put("r1", thread_id="t1")
        await repo.delete("r1")
        assert await repo.get("r1") is None

    @pytest.mark.anyio
    async def test_delete_missing_is_noop(self, repo):
        await repo.delete("nope")  # must not raise

    @pytest.mark.anyio
    async def test_update_model_name_normalizes(self, repo):
        await repo.put("r1", thread_id="t1", model_name="  minimax-m3  ")
        await repo.update_model_name("r1", "   MiniMax M3 With Way Too Long A Name " * 10)
        row = await repo.get("r1")
        # Production normalizer: strip() then truncate(128) if too long.
        # Bounded length + original prefix preserved.
        assert len(row["model_name"]) <= 128
        assert row["model_name"].startswith("MiniMax M3 With Way Too Long A Name")

    @pytest.mark.anyio
    async def test_update_model_name_trims_simple_input(self, repo):
        await repo.put("r1", thread_id="t1")
        await repo.update_model_name("r1", "  my-model  ")
        row = await repo.get("r1")
        # Short inputs get full strip() treatment, no truncation.
        assert row["model_name"] == "my-model"


class TestRunRepositoryListing:
    @pytest.mark.anyio
    async def test_list_by_thread_returns_only_matching_thread(self, repo):
        await repo.put("r1", thread_id="t1")
        await repo.put("r2", thread_id="t1")
        await repo.put("r3", thread_id="t2")
        rows = await repo.list_by_thread("t1")
        assert len(rows) == 2
        assert {r["run_id"] for r in rows} == {"r1", "r2"}

    @pytest.mark.anyio
    async def test_list_by_thread_filters_by_user(self, repo):
        await repo.put("r1", thread_id="t1", user_id="alice")
        await repo.put("r2", thread_id="t1", user_id="bob")
        rows = await repo.list_by_thread("t1", user_id="alice")
        assert len(rows) == 1
        assert rows[0]["run_id"] == "r1"

    @pytest.mark.anyio
    async def test_list_by_thread_owner_none_returns_all(self, repo):
        await repo.put("r1", thread_id="t1", user_id="alice")
        await repo.put("r2", thread_id="t1", user_id=None)
        rows = await repo.list_by_thread("t1", user_id=None)
        assert len(rows) == 2

    @pytest.mark.anyio
    async def test_list_by_thread_limit_applied(self, repo):
        for i in range(5):
            await repo.put(f"r{i}", thread_id="t1")
        rows = await repo.list_by_thread("t1", limit=3)
        assert len(rows) == 3

    @pytest.mark.anyio
    async def test_list_pending_returns_only_pending(self, repo):
        await repo.put("r1", thread_id="t1", status="pending")
        await repo.put("r2", thread_id="t1", status="running")
        await repo.put("r3", thread_id="t2", status="pending")
        rows = await repo.list_pending()
        assert len(rows) == 2
        assert {r["run_id"] for r in rows} == {"r1", "r3"}

    @pytest.mark.anyio
    async def test_list_inflight_returns_pending_and_running(self, repo):
        await repo.put("r1", thread_id="t1", status="pending")
        await repo.put("r2", thread_id="t1", status="running")
        await repo.put("r3", thread_id="t1", status="success")
        rows = await repo.list_inflight()
        assert {r["run_id"] for r in rows} == {"r1", "r2"}

    @pytest.mark.anyio
    async def test_list_pending_fifo_order_by_created_at(self, repo):
        """list_pending returns oldest pending first for crash-recovery queueing."""
        now = datetime.now(UTC)
        await repo.put("r_oldest", thread_id="t1", status="pending", created_at=(now - timedelta(hours=2)).isoformat())
        await repo.put("r_middle", thread_id="t1", status="pending", created_at=(now - timedelta(hours=1)).isoformat())
        await repo.put("r_newest", thread_id="t1", status="pending", created_at=now.isoformat())
        rows = await repo.list_pending()
        assert [r["run_id"] for r in rows] == ["r_oldest", "r_middle", "r_newest"]


class TestRunRepositoryUpdates:
    @pytest.mark.anyio
    async def test_update_run_completion_sets_token_counters(self, repo):
        await repo.put("r1", thread_id="t1")
        ok = await repo.update_run_completion(
            "r1",
            status="success",
            total_input_tokens=100,
            total_output_tokens=200,
            total_tokens=300,
            llm_call_count=5,
            lead_agent_tokens=200,
            subagent_tokens=50,
            middleware_tokens=50,
            token_usage_by_model={"minimax-m3": {"total_tokens": 300}},
            message_count=10,
            last_ai_message="Hello there",
            first_human_message="Hi",
        )
        assert ok is True
        row = await repo.get("r1")
        assert row["status"] == "success"
        assert row["total_input_tokens"] == 100
        assert row["total_output_tokens"] == 200
        assert row["total_tokens"] == 300
        assert row["llm_call_count"] == 5
        assert row["lead_agent_tokens"] == 200
        assert row["subagent_tokens"] == 50
        assert row["middleware_tokens"] == 50
        assert row["token_usage_by_model"] == {"minimax-m3": {"total_tokens": 300}}
        assert row["message_count"] == 10
        assert row["last_ai_message"] == "Hello there"
        assert row["first_human_message"] == "Hi"

    @pytest.mark.anyio
    async def test_update_run_completion_truncates_long_messages(self, repo):
        await repo.put("r1", thread_id="t1")
        long_msg = "x" * 5000
        await repo.update_run_completion("r1", status="success", last_ai_message=long_msg, first_human_message=long_msg)
        row = await repo.get("r1")
        # Column is TEXT (unlimited), but update_run_completion caps at 2000 chars
        # to keep listing pages snappy.
        assert len(row["last_ai_message"]) == 2000
        assert len(row["first_human_message"]) == 2000

    @pytest.mark.anyio
    async def test_update_run_completion_returns_false_for_missing(self, repo):
        assert await repo.update_run_completion("nope", status="success") is False

    @pytest.mark.anyio
    async def test_update_run_progress_requires_running_status(self, repo):
        """Progress updates only apply to running rows; pending/success are skipped."""
        await repo.put("r1", thread_id="t1", status="pending")
        await repo.update_run_progress("r1", total_tokens=999)
        row = await repo.get("r1")
        # Status unchanged, token counter not applied.
        assert row["status"] == "pending"
        assert row["total_tokens"] == 0

    @pytest.mark.anyio
    async def test_update_run_progress_partial_update(self, repo):
        await repo.put("r1", thread_id="t1", status="running")
        await repo.update_run_progress("r1", total_input_tokens=42, message_count=7)
        row = await repo.get("r1")
        assert row["total_input_tokens"] == 42
        assert row["message_count"] == 7
        # Other counters remain default.
        assert row["total_output_tokens"] == 0


class TestRunRepositoryAggregation:
    @pytest.mark.anyio
    async def test_aggregate_sums_totals_across_runs(self, repo):
        await repo.put("r1", thread_id="t1")
        await repo.update_run_completion("r1", status="success", total_input_tokens=100, total_output_tokens=200, total_tokens=300)
        await repo.put("r2", thread_id="t1")
        await repo.update_run_completion("r2", status="error", total_input_tokens=50, total_output_tokens=75, total_tokens=125)
        agg = await repo.aggregate_tokens_by_thread("t1")
        assert agg["total_input_tokens"] == 150
        assert agg["total_output_tokens"] == 275
        assert agg["total_tokens"] == 425
        assert agg["total_runs"] == 2

    @pytest.mark.anyio
    async def test_aggregate_by_model_uses_json_column(self, repo):
        await repo.put("r1", thread_id="t1")
        await repo.update_run_completion(
            "r1",
            status="success",
            total_tokens=1000,
            token_usage_by_model={"minimax-m3": {"total_tokens": 700}, "claude-opus": {"total_tokens": 300}},
        )
        agg = await repo.aggregate_tokens_by_thread("t1")
        assert agg["by_model"]["minimax-m3"]["tokens"] == 700
        assert agg["by_model"]["minimax-m3"]["runs"] == 1
        assert agg["by_model"]["claude-opus"]["tokens"] == 300

    @pytest.mark.anyio
    async def test_aggregate_falls_back_to_model_name_when_json_empty(self, repo):
        """Rows written before token_usage_by_model existed still aggregate by model_name."""
        await repo.put("r1", thread_id="t1", model_name="legacy-model")
        await repo.update_run_completion("r1", status="success", total_tokens=500)
        agg = await repo.aggregate_tokens_by_thread("t1")
        assert agg["by_model"]["legacy-model"]["tokens"] == 500

    @pytest.mark.anyio
    async def test_aggregate_excludes_active_runs_by_default(self, repo):
        """include_active=False skips running/pending; only success/error counted."""
        await repo.put("r_running", thread_id="t1", status="running")
        await repo.update_run_progress("r_running", total_tokens=999)
        agg = await repo.aggregate_tokens_by_thread("t1", include_active=False)
        # Running row excluded from aggregates entirely (total_runs=0).
        assert agg["total_runs"] == 0

        agg_with_active = await repo.aggregate_tokens_by_thread("t1", include_active=True)
        assert agg_with_active["total_runs"] == 1

    @pytest.mark.anyio
    async def test_aggregate_by_caller_bucket(self, repo):
        await repo.put("r1", thread_id="t1")
        await repo.update_run_completion(
            "r1",
            status="success",
            lead_agent_tokens=100,
            subagent_tokens=30,
            middleware_tokens=20,
        )
        agg = await repo.aggregate_tokens_by_thread("t1")
        assert agg["by_caller"] == {
            "lead_agent": 100,
            "subagent": 30,
            "middleware": 20,
        }


class TestRunRepositoryOwnerIsolation:
    @pytest.mark.anyio
    async def test_get_returns_none_when_user_mismatch(self, repo):
        await repo.put("r1", thread_id="t1", user_id="alice")
        # Bob asks for r1 → must not see it.
        assert await repo.get("r1", user_id="bob") is None
        # Alice can see her own.
        assert await repo.get("r1", user_id="alice") is not None

    @pytest.mark.anyio
    async def test_delete_is_a_noop_when_user_mismatch(self, repo):
        await repo.put("r1", thread_id="t1", user_id="alice")
        await repo.delete("r1", user_id="bob")
        # Row still exists.
        assert await repo.get("r1", user_id="alice") is not None


class TestRunRepositorySafeJson:
    """Direct tests on the static _safe_json normalizer (no DB needed)."""

    def test_safe_json_handles_datetime(self):
        """datetime is not JSON-serializable; falls back to str()."""
        dt = datetime(2024, 1, 15, 10, 30, 0, tzinfo=UTC)
        result = RunRepository._safe_json(dt)
        # json.dumps(datetime) raises TypeError → str() fallback.
        assert result == str(dt)
        assert isinstance(result, str)

    def test_safe_json_handles_pydantic_model(self):
        from pydantic import BaseModel

        class Sample(BaseModel):
            foo: str
            bar: int

        result = RunRepository._safe_json(Sample(foo="hi", bar=42))
        assert result == {"foo": "hi", "bar": 42}

    def test_safe_json_handles_unserializable_fallback(self):
        """Objects that aren't JSON-serializable fall back to str()."""

        class NotSerializable:
            def __str__(self):
                return "fallback-string"

        result = RunRepository._safe_json(NotSerializable())
        assert result == "fallback-string"

    def test_safe_json_handles_nested_dicts(self):
        nested = {"a": {"b": {"c": [1, "two", datetime(2024, 1, 1, tzinfo=UTC)]}}}
        result = RunRepository._safe_json(nested)
        # Nested datetime at index [2] falls back to its string representation.
        assert result["a"]["b"]["c"][0] == 1
        assert result["a"]["b"]["c"][1] == "two"
        assert isinstance(result["a"]["b"]["c"][2], str)

    def test_safe_json_passes_through_primitives(self):
        assert RunRepository._safe_json("hello") == "hello"
        assert RunRepository._safe_json(42) == 42
        assert RunRepository._safe_json(None) is None
        assert RunRepository._safe_json(True) is True
