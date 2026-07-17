"""B3: add model_configs + agent_configs tables with backfill

Phase 3 — Per-user scoping of models & agents.
Backfills owner_id=NULL (share-by-default) from runtime_models.yaml
and the on-disk users/*/agents/* tree so existing data stays visible.

Revision ID: 2026_07_17_b3_scoping
Revises: 2026_07_16_credit_requests
Create Date: 2026-07-17
"""

from __future__ import annotations

import logging
import os
import sys
from pathlib import Path

import sqlalchemy as sa
import yaml
from alembic import op

logger = logging.getLogger(__name__)

revision = "2026_07_17_b3_scoping"
down_revision = "2026_07_16_credit_requests"
branch_labels = None
depends_on = None


# ---------------------------------------------------------------------------
# Helpers (pure functions, no imports that require the app runtime)
# ---------------------------------------------------------------------------


def _resolve_deerflow_home() -> Path:
    """Resolve DEER_FLOW_HOME the same way deerflow.config.paths does."""
    if home := os.getenv("DEER_FLOW_HOME"):
        return Path(home)
    from deerflow.config.runtime_paths import runtime_home

    return runtime_home()


def _load_runtime_model_entries(home: Path) -> list[dict]:
    """Load runtime model entries from runtime_models.yaml (same logic as runtime_models.py)."""
    path = home / "runtime_models.yaml"
    if not path.exists():
        return []
    try:
        with open(path, encoding="utf-8") as fh:
            data = yaml.safe_load(fh)
    except (OSError, yaml.YAMLError):
        logger.warning("Could not read runtime_models.yaml at %s — skipping backfill", path)
        return []
    if data is None:
        return []
    models = data.get("models") if isinstance(data, dict) else data
    if not isinstance(models, list):
        return []
    return [m for m in models if isinstance(m, dict)]


def _walk_agent_dirs(home: Path) -> list[tuple[str | None, str]]:
    """Walk per-user and legacy agent directories.

    Yields (owner_id, agent_name) tuples.
    - Per-user:  {home}/users/{user_id}/agents/{agent_name}/
    - Legacy:    {home}/agents/{agent_name}/
    """
    results: list[tuple[str | None, str]] = []

    # Per-user agents
    users_dir = home / "users"
    if users_dir.is_dir():
        for user_dir in users_dir.iterdir():
            if not user_dir.is_dir():
                continue
            agents_dir = user_dir / "agents"
            if not agents_dir.is_dir():
                continue
            for agent_dir in agents_dir.iterdir():
                if agent_dir.is_dir() and (agent_dir / "config.yaml").exists():
                    results.append((user_dir.name, agent_dir.name))

    # Legacy shared agents
    legacy_dir = home / "agents"
    if legacy_dir.is_dir():
        for agent_dir in legacy_dir.iterdir():
            if agent_dir.is_dir() and (agent_dir / "config.yaml").exists():
                results.append((None, agent_dir.name))

    return results


# ---------------------------------------------------------------------------
# Tables — raw SQL (engine-agnostic so both Alembic and tests call the same fn)
# ---------------------------------------------------------------------------


def _create_tables(bind) -> None:
    with bind.begin() as conn:
        conn.execute(sa.text("""
            CREATE TABLE IF NOT EXISTS model_configs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                owner_id VARCHAR(64),
                name VARCHAR(128) NOT NULL,
                model VARCHAR(256) NOT NULL,
                model_use VARCHAR(256) DEFAULT 'langchain_openai:ChatOpenAI',
                display_name VARCHAR(256),
                description VARCHAR(1024),
                base_url VARCHAR(512),
                has_api_key INTEGER DEFAULT 0,
                supports_thinking INTEGER DEFAULT 0,
                supports_reasoning_effort INTEGER DEFAULT 0,
                supports_vision INTEGER DEFAULT 0,
                amd_compute VARCHAR(256),
                is_shared INTEGER DEFAULT 0,
                is_system INTEGER DEFAULT 0,
                created_at DATETIME DEFAULT CURRENT_TIMESTAMP NOT NULL,
                updated_at DATETIME DEFAULT CURRENT_TIMESTAMP NOT NULL
            )
        """))
        conn.execute(sa.text("CREATE INDEX IF NOT EXISTS ix_model_configs_owner_id ON model_configs (owner_id)"))
        conn.execute(sa.text("CREATE UNIQUE INDEX IF NOT EXISTS uq_model_config_owner_name ON model_configs (owner_id, name)"))

        conn.execute(sa.text("""
            CREATE TABLE IF NOT EXISTS agent_configs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                owner_id VARCHAR(64),
                name VARCHAR(128) NOT NULL,
                is_shared INTEGER DEFAULT 0,
                is_system INTEGER DEFAULT 0,
                created_at DATETIME DEFAULT CURRENT_TIMESTAMP NOT NULL,
                updated_at DATETIME DEFAULT CURRENT_TIMESTAMP NOT NULL
            )
        """))
        conn.execute(sa.text("CREATE INDEX IF NOT EXISTS ix_agent_configs_owner_id ON agent_configs (owner_id)"))
        conn.execute(sa.text("CREATE UNIQUE INDEX IF NOT EXISTS uq_agent_config_owner_name ON agent_configs (owner_id, name)"))


def _drop_tables(bind) -> None:
    with bind.begin() as conn:
        conn.execute(sa.text("DROP INDEX IF EXISTS uq_agent_config_owner_name"))
        conn.execute(sa.text("DROP INDEX IF EXISTS ix_agent_configs_owner_id"))
        conn.execute(sa.text("DROP TABLE IF EXISTS agent_configs"))
        conn.execute(sa.text("DROP INDEX IF EXISTS uq_model_config_owner_name"))
        conn.execute(sa.text("DROP INDEX IF EXISTS ix_model_configs_owner_id"))
        conn.execute(sa.text("DROP TABLE IF EXISTS model_configs"))


# ---------------------------------------------------------------------------
# Upgrade / Downgrade
# ---------------------------------------------------------------------------


def upgrade() -> None:
    bind = op.get_bind()
    _upgrade_with_bind(bind)


def downgrade() -> None:
    bind = op.get_bind()
    _downgrade_with_bind(bind)


def _downgrade_with_bind(bind) -> None:
    """Core downgrade logic — accepts an explicit SQLAlchemy bind for testability."""
    _drop_tables(bind)
    logger.info("Tables model_configs and agent_configs dropped")


# ---------------------------------------------------------------------------
# Testable core (binds passed explicitly so tests can inject a test engine)
# ---------------------------------------------------------------------------


def _upgrade_with_bind(bind) -> None:
    """Core upgrade logic — accepts an explicit SQLAlchemy bind for testability."""
    inspector = sa.inspect(bind)
    existing = set(inspector.get_table_names())

    if "model_configs" in existing:
        logger.info("model_configs already exists — skipping")
        return

    _create_tables(bind)
    logger.info("Tables model_configs and agent_configs created")

    # Backfill model configs from runtime_models.yaml
    home = _resolve_deerflow_home()
    entries = _load_runtime_model_entries(home)
    if entries:
        with bind.begin() as conn:
            for entry in entries:
                conn.execute(
                    sa.text(
                        """
                        INSERT INTO model_configs
                            (owner_id, name, model, model_use, display_name, description, base_url,
                             has_api_key, supports_thinking, supports_reasoning_effort,
                             supports_vision, amd_compute, is_shared, is_system)
                        VALUES
                            (NULL, :name, :model, :model_use, :display_name, :description, :base_url,
                             :has_api_key, :supports_thinking, :supports_reasoning_effort,
                             :supports_vision, :amd_compute, 0, 0)
                        """
                    ),
                    {
                        "name": entry.get("name", ""),
                        "model": entry.get("model", ""),
                        "model_use": entry.get("use", "langchain_openai:ChatOpenAI"),
                        "display_name": entry.get("display_name"),
                        "description": entry.get("description"),
                        "base_url": entry.get("base_url"),
                        "has_api_key": bool(entry.get("api_key")),
                        "supports_thinking": bool(entry.get("supports_thinking")),
                        "supports_reasoning_effort": bool(entry.get("supports_reasoning_effort")),
                        "supports_vision": bool(entry.get("supports_vision")),
                        "amd_compute": entry.get("amd_compute"),
                    },
                )
        logger.info("Backfilled %d model entries from runtime_models.yaml", len(entries))
    else:
        logger.info("No runtime_models.yaml found — skipping model backfill")

    # Backfill agent configs from on-disk directories
    agents = _walk_agent_dirs(home)
    if agents:
        with bind.begin() as conn:
            for owner_id, name in agents:
                conn.execute(
                    sa.text(
                        "INSERT INTO agent_configs (owner_id, name, is_shared, is_system) VALUES (:owner_id, :name, 0, 0)"
                    ),
                    {"owner_id": owner_id, "name": name},
                )
        logger.info("Backfilled %d agent entries from disk", len(agents))
    else:
        logger.info("No agent directories found — skipping agent backfill")
