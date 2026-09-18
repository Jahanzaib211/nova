"""The boot-time schema contract (audit finding PROD-001 / GATE-MIGRATE).

Tables are created with ``Base.metadata.create_all`` at boot and Alembic
versions exist, but nothing ran them on deploy — so a column added to an
existing table never reached a live database. The chain also *assumes*
``create_all`` ran first: the earliest version alters ``runs`` and fails on an
empty database. The contract is therefore:

1. ``create_all`` creates any table that does not exist yet (new tables need
   no migration to appear on a fresh install);
2. ``alembic upgrade head`` then applies column-level changes to tables that
   already existed, every version guarded with ``inspect()`` so re-running
   on a database that already has the change is a no-op;
3. the result must equal ``Base.metadata`` exactly.

``docker/dev-entrypoint.sh`` runs both steps in that order (``NOVA_DB_MIGRATE``).
``test_schema_snapshot`` closes the remaining gap — a model column with no
migration — by pinning the ORM schema to ``contracts/schema.baseline.json``.
"""

from __future__ import annotations

from pathlib import Path

from alembic import command
from alembic.autogenerate import compare_metadata
from alembic.config import Config
from alembic.migration import MigrationContext
from sqlalchemy import create_engine

from deerflow.persistence.base import Base

_MIGRATIONS = Path(__file__).resolve().parents[1] / "packages" / "harness" / "deerflow" / "persistence" / "migrations"


def _create_all(db_path: Path) -> None:
    import deerflow.persistence.models  # noqa: F401 - registers every model

    engine = create_engine(f"sqlite:///{db_path}")
    Base.metadata.create_all(engine)
    engine.dispose()


def _upgrade_head(db_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("DEER_FLOW_DATABASE_URL", f"sqlite+aiosqlite:///{db_path}")
    cfg = Config(str(_MIGRATIONS / "alembic.ini"))
    cfg.set_main_option("script_location", str(_MIGRATIONS))
    command.upgrade(cfg, "head")


def _diff(db_path: Path) -> list:
    import deerflow.persistence.models  # noqa: F401 - registers every model

    engine = create_engine(f"sqlite:///{db_path}")
    with engine.connect() as conn:
        ctx = MigrationContext.configure(conn, opts={"compare_type": True})
        return compare_metadata(ctx, Base.metadata)


def test_create_all_then_head_matches_orm_metadata(tmp_path, monkeypatch):
    db = tmp_path / "head.db"
    _create_all(db)
    _upgrade_head(db, monkeypatch)
    diff = _diff(db)
    assert diff == [], "alembic head and Base.metadata disagree:\n" + "\n".join(str(d) for d in diff)


def test_upgrade_is_idempotent(tmp_path, monkeypatch):
    """Running head twice (the first live run lands on an existing DB) is safe."""
    db = tmp_path / "twice.db"
    _create_all(db)
    _upgrade_head(db, monkeypatch)
    _upgrade_head(db, monkeypatch)
    assert _diff(db) == []


def test_head_requires_create_all_first(tmp_path, monkeypatch):
    """Documents the contract: the chain is not a from-scratch schema."""
    import pytest
    from sqlalchemy.exc import NoSuchTableError

    with pytest.raises(NoSuchTableError):
        _upgrade_head(tmp_path / "empty.db", monkeypatch)


def test_revision_ids_fit_the_version_table():
    """Alembic's default ``alembic_version.version_num`` is VARCHAR(32); Postgres
    enforces it (SQLite does not), and several revision ids here are longer.
    env.py widens the column to 128 before the first migration runs; keep
    ids under that, and never rely on the SQLite run to prove a Postgres one.
    """
    from alembic.script import ScriptDirectory

    cfg = Config(str(_MIGRATIONS / "alembic.ini"))
    cfg.set_main_option("script_location", str(_MIGRATIONS))
    ids = [rev.revision for rev in ScriptDirectory.from_config(cfg).walk_revisions()]
    assert ids, "no revisions found"
    assert max(len(i) for i in ids) > 32, "the widening in env.py is only justified while ids exceed 32 chars"
    assert all(len(i) <= 128 for i in ids)


def test_version_table_is_widened(tmp_path, monkeypatch):
    from sqlalchemy import inspect

    db = tmp_path / "wide.db"
    _create_all(db)
    _upgrade_head(db, monkeypatch)
    engine = create_engine(f"sqlite:///{db}")
    col = next(c for c in inspect(engine).get_columns("alembic_version") if c["name"] == "version_num")
    assert getattr(col["type"], "length", None) == 128
