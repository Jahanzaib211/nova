"""Harness tokens: how an external harness (Claude Code, OpenClaw, a script)
authenticates to Nova's MCP server and the ops API as a specific user.

The plaintext is shown once at creation; only a SHA-256 hash is stored.
Tokens are scoped to capability modules, revocable one at a time, and
revoking is immediate (there is no cache to wait out).
"""

from __future__ import annotations

import pytest

from deerflow.persistence.harness_token.sql import HarnessTokenRepository, hash_token, is_token_shaped

pytestmark = pytest.mark.no_auto_user


async def _repo(tmp_path) -> HarnessTokenRepository:
    from deerflow.persistence.engine import get_session_factory, init_engine

    await init_engine("sqlite", url=f"sqlite+aiosqlite:///{tmp_path / 'ht.db'}", sqlite_dir=str(tmp_path))
    return HarnessTokenRepository(get_session_factory())


@pytest.fixture(autouse=True)
def _close_engine_after_test():
    yield
    import asyncio

    from deerflow.persistence.engine import close_engine

    asyncio.run(close_engine())


@pytest.mark.anyio
async def test_create_returns_plaintext_once_and_stores_only_a_hash(tmp_path):
    repo = await _repo(tmp_path)
    created = await repo.create(owner_user_id="u1", name="claude-code", scopes=["jobs", "models"])
    assert created["token"].startswith("nhk_") and is_token_shaped(created["token"])
    rows = await repo.list(owner_user_id="u1")
    assert len(rows) == 1
    assert "token" not in rows[0]
    assert rows[0]["prefix"] == created["token"][:12]
    assert rows[0]["scopes"] == ["jobs", "models"]
    assert rows[0]["revoked_at"] is None


@pytest.mark.anyio
async def test_resolve_returns_owner_and_scopes_and_touches_last_used(tmp_path):
    repo = await _repo(tmp_path)
    created = await repo.create(owner_user_id="u1", name="t", scopes=["*"])
    resolved = await repo.resolve(created["token"])
    assert resolved is not None
    assert resolved["owner_user_id"] == "u1" and resolved["scopes"] == ["*"]
    assert (await repo.list(owner_user_id="u1"))[0]["last_used_at"] is not None
    assert await repo.resolve("nhk_" + "x" * 43) is None
    assert await repo.resolve("garbage") is None


@pytest.mark.anyio
async def test_revoke_is_immediate_and_owner_scoped(tmp_path):
    repo = await _repo(tmp_path)
    created = await repo.create(owner_user_id="u1", name="t", scopes=["*"])
    assert await repo.revoke(created["id"], owner_user_id="u2") is False
    assert await repo.revoke(created["id"], owner_user_id="u1") is True
    assert await repo.resolve(created["token"]) is None
    assert (await repo.list(owner_user_id="u1"))[0]["revoked_at"] is not None


def test_hash_is_deterministic_and_not_the_token():
    assert hash_token("nhk_abc") == hash_token("nhk_abc")
    assert hash_token("nhk_abc") != "nhk_abc" and len(hash_token("nhk_abc")) == 64
