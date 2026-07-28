"""Tests for B3 migration: model_configs + agent_configs tables with backfill."""

from __future__ import annotations

import os
import shutil
import tempfile
from pathlib import Path

import pytest
import yaml
from sqlalchemy import create_engine, inspect, text


class TestB3Migration:
    """Test migration upgrade/downgrade by calling the functions directly."""

    @pytest.fixture
    def engine(self, tmp_path):
        """Fresh in-memory SQLite engine — no ORM tables pre-created.

        Simulates a clean DB that already has the pre-B3 schema (credit_requests
        and earlier), so the migration must create + backfill model_configs
        and agent_configs from scratch.
        """
        return create_engine("sqlite:///:memory:")

    def _run_upgrade(self, engine, home_path: Path) -> None:
        """Run the B3 migration upgrade on the given engine."""
        import sys

        sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "packages" / "harness"))
        from deerflow.persistence.migrations.versions import b3_scoping_2026_07_17 as mig

        old = os.environ.get("DEER_FLOW_HOME")
        os.environ["DEER_FLOW_HOME"] = str(home_path)
        try:
            mig._upgrade_with_bind(engine)
        finally:
            if old is not None:
                os.environ["DEER_FLOW_HOME"] = old
            elif "DEER_FLOW_HOME" in os.environ:
                del os.environ["DEER_FLOW_HOME"]

    def _run_downgrade(self, engine) -> None:
        import sys

        sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "packages" / "harness"))
        from deerflow.persistence.migrations.versions import b3_scoping_2026_07_17 as mig

        mig._downgrade_with_bind(engine)

    def test_upgrade_creates_tables(self, engine):
        """Both tables are created by upgrade()."""
        # Create a home dir so the migration doesn't try to walk anything
        home = Path(tempfile.mkdtemp())
        try:
            self._run_upgrade(engine, home)
            tables = inspect(engine).get_table_names()
            assert "model_configs" in tables
            assert "agent_configs" in tables
        finally:
            shutil.rmtree(home)

    def test_model_backfill_from_yaml(self, engine):
        """Runtime models are backfilled with owner_id=NULL."""
        home = Path(tempfile.mkdtemp()) / ".deer-flow"
        home.mkdir()
        (home / "runtime_models.yaml").write_text(
            yaml.safe_dump(
                {
                    "models": [
                        {
                            "name": "test-model-1",
                            "model": "gpt-4o",
                            "use": "langchain_openai:ChatOpenAI",
                            "display_name": "Test Model 1",
                            "supports_thinking": True,
                            "api_key": "sekret",
                        },
                        {"name": "test-model-2", "model": "claude-3-sonnet"},
                    ]
                }
            )
        )
        try:
            self._run_upgrade(engine, home)
            with engine.connect() as conn:
                rows = conn.execute(text("SELECT name, model, has_api_key, supports_thinking, owner_id FROM model_configs")).fetchall()
            assert len(rows) == 2
            by_name = {r.name: r for r in rows}
            assert by_name["test-model-1"].model == "gpt-4o"
            assert by_name["test-model-1"].has_api_key == 1
            assert by_name["test-model-1"].supports_thinking == 1
            assert by_name["test-model-1"].owner_id is None  # global
            assert by_name["test-model-2"].has_api_key == 0
            assert by_name["test-model-2"].owner_id is None
        finally:
            shutil.rmtree(home.parent)

    def test_agent_backfill_from_disk(self, engine):
        """Agent directories are backfilled with correct owner_id."""
        home = Path(tempfile.mkdtemp()) / ".deer-flow"
        home.mkdir()
        # Per-user agents
        for user, agent in [("alice", "alice-bot"), ("bob", "bob-bot")]:
            d = home / "users" / user / "agents" / agent
            d.mkdir(parents=True)
            (d / "config.yaml").write_text(yaml.safe_dump({"name": agent}))
        # Legacy shared agent
        legacy = home / "agents" / "shared-bot"
        legacy.mkdir(parents=True)
        (legacy / "config.yaml").write_text(yaml.safe_dump({"name": "shared-bot"}))

        try:
            self._run_upgrade(engine, home)
            with engine.connect() as conn:
                rows = conn.execute(text("SELECT owner_id, name FROM agent_configs")).fetchall()
            assert len(rows) == 3
            by_name = {r.name: r for r in rows}
            assert by_name["alice-bot"].owner_id == "alice"
            assert by_name["bob-bot"].owner_id == "bob"
            assert by_name["shared-bot"].owner_id is None  # legacy → NULL
        finally:
            shutil.rmtree(home.parent)

    def test_downgrade_drops_tables(self, engine):
        """Downgrade drops both tables."""
        home = Path(tempfile.mkdtemp())
        try:
            self._run_upgrade(engine, home)
            assert "model_configs" in inspect(engine).get_table_names()
            self._run_downgrade(engine)
            assert "model_configs" not in inspect(engine).get_table_names()
            assert "agent_configs" not in inspect(engine).get_table_names()
        finally:
            shutil.rmtree(home)

    def test_idempotent_upgrade(self, engine):
        """Re-running upgrade leaves existing data intact."""
        home = Path(tempfile.mkdtemp()) / ".deer-flow"
        home.mkdir()
        (home / "runtime_models.yaml").write_text(yaml.safe_dump({"models": [{"name": "m1", "model": "gpt-4o"}]}))
        try:
            self._run_upgrade(engine, home)
            with engine.connect() as conn:
                count1 = conn.execute(text("SELECT COUNT(*) FROM model_configs")).scalar()

            # Run again — should be idempotent
            self._run_upgrade(engine, home)
            with engine.connect() as conn:
                count2 = conn.execute(text("SELECT COUNT(*) FROM model_configs")).scalar()

            assert count1 == count2 == 1
        finally:
            shutil.rmtree(home.parent)

    def test_empty_runtime_models_skips_backfill(self, engine):
        """No runtime_models.yaml → no crash, zero model rows."""
        home = Path(tempfile.mkdtemp()) / ".deer-flow"
        home.mkdir()
        try:
            self._run_upgrade(engine, home)
            with engine.connect() as conn:
                count = conn.execute(text("SELECT COUNT(*) FROM model_configs")).scalar()
            assert count == 0
        finally:
            shutil.rmtree(home.parent)
