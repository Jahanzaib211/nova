"""Router tests for public read-only thread sharing.

POST /threads/{id}/share (owner-gated) stores an unguessable token in
``shared_threads``; DELETE revokes it; GET /share/{token} is public and
returns a sanitized message view — every field except ``event_type``,
``content``, ``created_at``, ``seq`` and ``run_id`` (notably the raw
``metadata`` blob) must be absent.
"""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from _router_auth_helpers import make_authed_test_app
from fastapi.testclient import TestClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.gateway.routers import sharing
from deerflow.persistence.base import Base
from deerflow.persistence.shared_thread.model import SharedThreadRow
from deerflow.runtime.events.store.memory import MemoryRunEventStore

ALLOWED_KEYS = {"event_type", "content", "created_at", "seq", "run_id"}


@pytest.fixture()
def db_session_factory():
    """In-memory SQLite factory for the ``shared_threads`` table."""
    engine = create_async_engine("sqlite+aiosqlite://")
    sf = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

    async def _setup() -> None:
        # Importing SharedThreadRow registers it with Base.metadata.
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)

    asyncio.run(_setup())
    with patch("app.gateway.routers.sharing.get_session_factory", return_value=sf):
        yield sf
    asyncio.run(engine.dispose())


def _make_app(*, owner_check_passes: bool = True) -> tuple[TestClient, MemoryRunEventStore]:
    """Authed test app wired with a memory event store and permissive meta store."""
    app = make_authed_test_app(owner_check_passes=owner_check_passes)

    thread_store = MagicMock()
    thread_store.check_access = AsyncMock(return_value=owner_check_passes)
    thread_store.get = AsyncMock(return_value={"display_name": "Shared Chat"})
    app.state.thread_store = thread_store

    event_store = MemoryRunEventStore()
    app.state.run_event_store = event_store
    app.include_router(sharing.router)
    return TestClient(app), event_store


def _seed_messages(event_store: MemoryRunEventStore, thread_id: str) -> None:
    asyncio.run(
        event_store.put(
            thread_id=thread_id,
            run_id="run-1",
            event_type="human_message",
            category="message",
            content={"type": "human", "content": "Hello"},
            metadata={"private_note": "must-not-leak"},
        )
    )
    asyncio.run(
        event_store.put(
            thread_id=thread_id,
            run_id="run-1",
            event_type="ai_message",
            category="message",
            content="Hi there!",
        )
    )


def test_share_roundtrip_is_public_and_sanitized(db_session_factory):
    client, event_store = _make_app()
    _seed_messages(event_store, "thread-1")

    created = client.post("/api/threads/thread-1/share")
    assert created.status_code == 200
    body = created.json()
    assert body["shared"] is True
    assert body["thread_id"] == "thread-1"
    assert len(body["token"]) == 32

    shared = client.get(f"/api/share/{body['token']}")
    assert shared.status_code == 200
    view = shared.json()
    assert view["thread_id"] == "thread-1"
    assert view["thread_title"] == "Shared Chat"
    assert len(view["messages"]) == 2
    for message in view["messages"]:
        assert set(message.keys()) == ALLOWED_KEYS
        assert "metadata" not in message
    assert view["messages"][0]["content"] == {"type": "human", "content": "Hello"}
    assert view["messages"][1]["content"] == "Hi there!"
    assert view["messages"][0]["event_type"] == "human_message"

    revoked = client.delete("/api/threads/thread-1/share")
    assert revoked.status_code == 200
    assert revoked.json()["shared"] is False

    gone = client.get(f"/api/share/{body['token']}")
    assert gone.status_code == 404


def test_share_link_is_idempotent(db_session_factory):
    client, _ = _make_app()

    first = client.post("/api/threads/thread-1/share").json()
    second = client.post("/api/threads/thread-1/share").json()

    assert first["token"] == second["token"]
    assert second["shared"] is True


def test_share_requires_thread_write_permission(db_session_factory):
    client, _ = _make_app(owner_check_passes=False)

    denied = client.post("/api/threads/thread-1/share")
    # Owner-check deny is strict-deny: an existing row with a different
    # owner surfaces as 404 (existence is not leaked, per authz.py).
    assert denied.status_code == 404


def test_revoke_without_existing_share_is_noop(db_session_factory):
    client, _ = _make_app()

    revoked = client.delete("/api/threads/thread-1/share")
    assert revoked.status_code == 200
    body = revoked.json()
    assert body["shared"] is False
    assert body["token"] == ""


def test_unknown_token_returns_404(db_session_factory):
    client, _ = _make_app()

    missing = client.get("/api/share/does-not-exist")
    assert missing.status_code == 404


def test_public_view_survives_missing_thread_store(db_session_factory):
    client, event_store = _make_app()
    _seed_messages(event_store, "thread-2")
    client.post("/api/threads/thread-2/share")

    import app.gateway.routers.sharing as router_module

    sf = router_module.get_session_factory()
    row = None

    async def _lookup() -> None:
        nonlocal row
        async with sf() as session:
            from sqlalchemy import select

            row = (await session.execute(select(SharedThreadRow).where(SharedThreadRow.thread_id == "thread-2"))).scalar_one()

    asyncio.run(_lookup())

    app = client.app
    app.state.thread_store = None
    shared = client.get(f"/api/share/{row.token}")
    assert shared.status_code == 200
    assert shared.json()["thread_title"] is None
