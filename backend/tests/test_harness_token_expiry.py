"""Per-turn harness tokens expire, and stale ones are swept at startup.

An ACP session revokes its ``runtime:<thread>`` token when it closes, but a
revoke that failed (cancelled session, database blip, gateway reload mid-turn)
left a bearer token for the user's sandbox live forever: the table had no
expiry. Pinned here:

* ``create(ttl_seconds=...)`` sets ``expires_at``; ``resolve`` rejects an
  expired token (so the MCP server answers 401);
* tokens minted by an ACP session always expire (turn budget + margin), even
  through a factory that predates the TTL parameter;
* the startup sweep revokes expired runtime tokens and pre-expiry ones older
  than a token lifetime, and never touches human-minted tokens or a young
  runtime token that may belong to a live session in another process;
* the migration adds and drops the column (up/down) on a database that
  predates it.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from sqlalchemy import create_engine, inspect, update

from nova.persistence.harness_token.model import HarnessTokenRow
from nova.persistence.harness_token.sql import HarnessTokenRepository, is_expired

pytestmark = pytest.mark.no_auto_user


async def _repo(tmp_path) -> HarnessTokenRepository:
    from nova.persistence.engine import get_session_factory, init_engine

    await init_engine("sqlite", url=f"sqlite+aiosqlite:///{tmp_path / 'ht.db'}", sqlite_dir=str(tmp_path))
    return HarnessTokenRepository(get_session_factory())


@pytest.fixture(autouse=True)
def _close_engine_after_test():
    yield
    import asyncio

    from nova.persistence.engine import close_engine

    asyncio.run(close_engine())


async def _set(repo: HarnessTokenRepository, token_id: str, **values) -> None:
    async with repo._sf() as session:
        await session.execute(update(HarnessTokenRow).where(HarnessTokenRow.id == token_id).values(**values))
        await session.commit()


def test_is_expired_treats_naive_as_utc():
    past = datetime.now(UTC).replace(tzinfo=None) - timedelta(seconds=5)
    assert is_expired(past)
    assert not is_expired(None)
    assert not is_expired(datetime.now(UTC) + timedelta(minutes=1))


@pytest.mark.anyio
async def test_ttl_sets_expiry_and_expired_tokens_do_not_resolve(tmp_path):
    repo = await _repo(tmp_path)
    created = await repo.create(owner_user_id="u1", name="runtime:t1", scopes=["sandbox"], ttl_seconds=60)
    assert created["expires_at"] is not None
    assert await repo.resolve(created["token"]) is not None

    await _set(repo, created["id"], expires_at=datetime.now(UTC) - timedelta(seconds=1))
    assert await repo.resolve(created["token"]) is None


@pytest.mark.anyio
async def test_tokens_without_ttl_never_expire(tmp_path):
    repo = await _repo(tmp_path)
    created = await repo.create(owner_user_id="u1", name="my laptop", scopes=["*"])
    assert created["expires_at"] is None
    assert await repo.resolve(created["token"]) is not None


@pytest.mark.anyio
async def test_mcp_server_rejects_an_expired_token(tmp_path):
    from app.gateway.mcp_server import NovaMcpServer
    from nova.capabilities import CapabilityRegistry

    repo = await _repo(tmp_path)
    created = await repo.create(owner_user_id="u1", name="runtime:t1", scopes=["sandbox"], ttl_seconds=60)
    server = NovaMcpServer(CapabilityRegistry(), repo, flags=lambda: {}, enabled=lambda: True)
    assert await server.authenticate(f"Bearer {created['token']}") is not None

    await _set(repo, created["id"], expires_at=datetime.now(UTC) - timedelta(seconds=1))
    assert await server.authenticate(f"Bearer {created['token']}") is None


@pytest.mark.anyio
async def test_startup_sweep_revokes_only_stale_runtime_tokens(tmp_path):
    repo = await _repo(tmp_path)
    old = datetime.now(UTC) - timedelta(days=2)
    expired = await repo.create(owner_user_id="u1", name="runtime:a", scopes=["sandbox"], ttl_seconds=60)
    await _set(repo, expired["id"], expires_at=datetime.now(UTC) - timedelta(seconds=1))
    legacy_old = await repo.create(owner_user_id="u1", name="runtime:b", scopes=["*"])
    await _set(repo, legacy_old["id"], created_at=old)
    legacy_young = await repo.create(owner_user_id="u1", name="runtime:c", scopes=["*"])
    live = await repo.create(owner_user_id="u1", name="runtime:d", scopes=["sandbox"], ttl_seconds=3600)
    human_old = await repo.create(owner_user_id="u1", name="claude-code laptop", scopes=["*"])
    await _set(repo, human_old["id"], created_at=old)

    from app.gateway.app import _sweep_stale_runtime_tokens

    assert await _sweep_stale_runtime_tokens(repo) == 2

    rows = {r["name"]: r for r in await repo.list(owner_user_id="u1")}
    assert rows["runtime:a"]["revoked_at"] and rows["runtime:b"]["revoked_at"]
    assert rows["runtime:c"]["revoked_at"] is None, "a young runtime token may belong to a live session elsewhere"
    assert rows["runtime:d"]["revoked_at"] is None
    assert rows["claude-code laptop"]["revoked_at"] is None, "human-minted tokens are never swept"
    assert await repo.resolve(live["token"]) is not None
    assert await repo.resolve(legacy_young["token"]) is not None
    assert await _sweep_stale_runtime_tokens(None) == 0


@pytest.mark.anyio
async def test_acp_session_mints_an_expiring_token(tmp_path, monkeypatch):
    from nova.runtimes import session as session_mod

    repo = await _repo(tmp_path)
    monkeypatch.setattr("nova.persistence.engine.get_session_factory", lambda: repo._sf)
    nova_server, _ = session_mod.default_nova_mcp("u1", "thread-1")
    minted = await session_mod._call_mint(nova_server["__mint__"], session_mod.runtime_token_ttl(120))
    expires = datetime.fromisoformat(minted["expires_at"])
    remaining = (expires - datetime.now(UTC)).total_seconds()
    assert 120 < remaining <= 120 + session_mod.TIMEOUT_GRACE_S + session_mod.TOKEN_TTL_MARGIN_S + 5

    # Called without the TTL (an older call site), it still expires.
    minted = await nova_server["__mint__"]()
    assert minted["expires_at"] is not None


@pytest.mark.anyio
async def test_call_mint_supports_factories_without_ttl():
    from nova.runtimes.session import _call_mint

    async def legacy():
        return {"id": "x"}

    seen = {}

    async def modern(ttl_seconds=None):
        seen["ttl"] = ttl_seconds
        return {"id": "y"}

    assert (await _call_mint(legacy, 99))["id"] == "x"
    assert (await _call_mint(modern, 99))["id"] == "y"
    assert seen["ttl"] == 99


# --- migration up/down ------------------------------------------------------

_MIGRATIONS = Path(__file__).resolve().parents[1] / "packages" / "harness" / "nova" / "persistence" / "migrations"


def _alembic(db: Path, monkeypatch, action: str, target: str) -> None:
    from alembic import command
    from alembic.config import Config

    monkeypatch.setenv("DEER_FLOW_DATABASE_URL", f"sqlite+aiosqlite:///{db}")
    cfg = Config(str(_MIGRATIONS / "alembic.ini"))
    cfg.set_main_option("script_location", str(_MIGRATIONS))
    getattr(command, action)(cfg, target)


def _columns(db: Path) -> set[str]:
    engine = create_engine(f"sqlite:///{db}")
    try:
        return {c["name"] for c in inspect(engine).get_columns("harness_tokens")}
    finally:
        engine.dispose()


def test_migration_adds_and_drops_expires_at(tmp_path, monkeypatch):
    from sqlalchemy import MetaData

    import nova.persistence.models  # noqa: F401
    from nova.persistence.base import Base

    db = tmp_path / "pre.db"
    engine = create_engine(f"sqlite:///{db}")
    older = MetaData()
    for table in Base.metadata.sorted_tables:
        copy = table.to_metadata(older)
        if table.name == "harness_tokens":
            copy._columns.remove(copy.c.expires_at)
    older.create_all(engine)
    engine.dispose()
    assert "expires_at" not in _columns(db)

    _alembic(db, monkeypatch, "upgrade", "head")
    assert "expires_at" in _columns(db)

    _alembic(db, monkeypatch, "downgrade", "2026_10_29_users_email_lower")
    assert "expires_at" not in _columns(db)
    assert {"id", "token_hash", "revoked_at"} <= _columns(db)

    _alembic(db, monkeypatch, "upgrade", "head")
    assert "expires_at" in _columns(db)
