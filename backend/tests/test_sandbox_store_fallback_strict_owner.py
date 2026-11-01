"""The sandbox router's store fallback requires the row to belong to the caller.

``check_access(require_existing=True)`` treats a row with ``user_id`` NULL as
shared and grants it to anyone. That is the right answer for some read paths,
but for the sandbox endpoints -- which start dev servers, list and read files
and proxy into the container -- an ownerless row would hand any authenticated
user a guessable thread id's sandbox. The router-side fallback therefore
demands ``owner == caller`` and refuses ownerless rows, except in the
auth-disabled single-user mode where every thread belongs to the one operator.
"""

from __future__ import annotations

import asyncio
import contextlib
from types import SimpleNamespace

import pytest

from app.gateway.routers import sandbox as sandbox_router
from deerflow.persistence.thread_meta.memory import MemoryThreadMetaStore
from deerflow.runtime.user_context import DEFAULT_USER_ID, reset_current_user, set_current_user


@pytest.fixture(autouse=True)
def _no_disk_thread_dir(tmp_path, monkeypatch):
    """No thread directory exists, so only the store fallback can answer."""
    root = tmp_path / "threads"
    monkeypatch.setattr(sandbox_router, "get_paths", lambda: SimpleNamespace(thread_dir=lambda tid, user_id=None: root / str(tid)))
    monkeypatch.delenv("DEER_FLOW_AUTH_DISABLED", raising=False)


@pytest.fixture
def store():
    from langgraph.store.memory import InMemoryStore

    return MemoryThreadMetaStore(InMemoryStore())


@contextlib.contextmanager
def _as_user(user_id: str):
    token = set_current_user(SimpleNamespace(id=user_id))
    try:
        yield
    finally:
        reset_current_user(token)


def _request_for(store):
    return SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace(thread_store=store)))


def _owns(thread_id, store) -> bool:
    return asyncio.run(sandbox_router._owns_thread_or_fresh(thread_id, _request_for(store)))


def test_the_owner_is_admitted(store):
    asyncio.run(store.create("t-owned", user_id="alice"))
    with _as_user("alice"):
        assert _owns("t-owned", store) is True


def test_another_user_is_refused(store):
    asyncio.run(store.create("t-owned", user_id="alice"))
    with _as_user("bob"):
        assert _owns("t-owned", store) is False


def test_an_ownerless_row_is_refused_to_an_authenticated_caller(store):
    asyncio.run(store.create("t-orphan", user_id=None))
    with _as_user("bob"):
        assert _owns("t-orphan", store) is False


def test_an_ownerless_row_is_refused_on_a_real_endpoint(store):
    from fastapi import HTTPException

    asyncio.run(store.create("t-orphan", user_id=None))
    with _as_user("bob"):
        with pytest.raises(HTTPException) as exc_info:
            asyncio.run(sandbox_router.dev_servers("t-orphan", _request_for(store)))
    assert exc_info.value.status_code == 404


def test_a_missing_row_is_refused(store):
    with _as_user("alice"):
        assert _owns("t-nowhere", store) is False


def test_no_store_is_a_refusal():
    with _as_user("alice"):
        assert asyncio.run(sandbox_router._owns_thread_or_fresh("t", None)) is False


def test_a_store_failure_is_a_refusal():
    class _Broken:
        async def get(self, *a, **k):
            raise RuntimeError("db down")

    with _as_user("alice"):
        assert _owns("t", _Broken()) is False


class TestAuthDisabledSingleUserMode:
    """With auth disabled every thread belongs to the one local operator."""

    @pytest.fixture(autouse=True)
    def _auth_disabled(self, monkeypatch):
        monkeypatch.setenv("DEER_FLOW_AUTH_DISABLED", "1")
        monkeypatch.delenv("DEER_FLOW_ENV", raising=False)
        monkeypatch.delenv("ENVIRONMENT", raising=False)

    def test_a_thread_created_in_no_auth_mode_is_admitted(self, store):
        # The auth-disabled middleware runs every request as DEFAULT_USER_ID,
        # and threads it creates are owned by that same id.
        asyncio.run(store.create("t-local", user_id=DEFAULT_USER_ID))
        with _as_user(DEFAULT_USER_ID):
            assert _owns("t-local", store) is True

    def test_an_ownerless_legacy_row_is_admitted(self, store):
        asyncio.run(store.create("t-legacy", user_id=None))
        with _as_user(DEFAULT_USER_ID):
            assert _owns("t-legacy", store) is True

    def test_a_missing_row_is_still_refused(self, store):
        assert _owns("t-nowhere", store) is False


def test_the_websocket_guard_refuses_an_ownerless_row(store, tmp_path, monkeypatch):
    """The voice and task/computer sockets go through ``ws_caller_owns_thread``,
    which used ``check_access`` and so admitted any caller to an ownerless row."""
    from app.gateway import ws_guards

    monkeypatch.setattr(ws_guards, "get_paths", lambda: SimpleNamespace(thread_dir=lambda tid, user_id=None: tmp_path / "none" / str(tid)))
    asyncio.run(store.create("t-orphan-ws", user_id=None))
    asyncio.run(store.create("t-mine-ws", user_id="alice"))
    websocket = SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace(thread_store=store)))
    with _as_user("mallory"):
        assert asyncio.run(ws_guards.ws_caller_owns_thread(websocket, "t-orphan-ws")) is False
        assert asyncio.run(ws_guards.ws_caller_owns_thread(websocket, "t-mine-ws")) is False
    with _as_user("alice"):
        assert asyncio.run(ws_guards.ws_caller_owns_thread(websocket, "t-mine-ws")) is True
