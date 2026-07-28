"""Tests for bring-your-own-key (BYOK): encryption, feature-flag gating,
the model-factory contextvar override, and the /api/v1/byok endpoints.
"""

import asyncio
import os

import pytest
from cryptography.fernet import Fernet
from fastapi.testclient import TestClient

os.environ.setdefault("AUTH_JWT_SECRET", "test-secret-key-byok-min-32-characters!!")

from app.gateway.auth.config import AuthConfig, set_auth_config

_TEST_SECRET = "test-secret-key-byok-min-32-characters!!"
_PASSWORD = "Tr0ub4dor3a"
_FERNET_KEY = Fernet.generate_key().decode()


@pytest.fixture()
def byok_env(monkeypatch):
    """Enable BYOK with a valid Fernet secret for the duration of a test."""
    monkeypatch.setenv("NOVA_BYOK_ENABLED", "1")
    monkeypatch.setenv("NOVA_BYOK_SECRET", _FERNET_KEY)


# ── Flag gating ──────────────────────────────────────────────────────────


def test_byok_disabled_by_default(monkeypatch):
    monkeypatch.delenv("NOVA_BYOK_ENABLED", raising=False)
    monkeypatch.delenv("NOVA_BYOK_SECRET", raising=False)
    from app.gateway import byok

    assert byok.byok_enabled() is False


def test_byok_requires_valid_secret(monkeypatch):
    monkeypatch.setenv("NOVA_BYOK_ENABLED", "1")
    monkeypatch.setenv("NOVA_BYOK_SECRET", "not-a-valid-fernet-key")
    from app.gateway import byok

    assert byok.byok_enabled() is False


def test_byok_enabled_with_flag_and_secret(byok_env):
    from app.gateway import byok

    assert byok.byok_enabled() is True


# ── Encryption round-trip ────────────────────────────────────────────────


def test_encrypt_decrypt_round_trip(byok_env):
    from app.gateway import byok

    token = byok.encrypt_key("sk-secret-123")
    assert token != "sk-secret-123"  # actually encrypted
    assert byok.decrypt_key(token) == "sk-secret-123"


# ── Model factory honours the contextvar override ────────────────────────


def test_factory_uses_byok_contextvar():
    """create_chat_model overrides api_key when the BYOK contextvar is set."""
    from types import SimpleNamespace

    from deerflow.models import factory
    from deerflow.runtime.byok_context import reset_byok_api_key, set_byok_api_key

    captured = {}

    class _FakeModel:
        def __init__(self, **kwargs):
            captured.update(kwargs)

        # BaseChatModel subclass check in factory: patch resolve_class instead.

    model_cfg = SimpleNamespace(
        name="m",
        use="x:Y",
        when_thinking_enabled=None,
        when_thinking_disabled=None,
        thinking=None,
        supports_thinking=False,
        supports_reasoning_effort=False,
        supports_vision=False,
        model_dump=lambda **_: {"api_key": "nova-key", "model": "gpt"},
    )
    app_cfg = SimpleNamespace(
        models=[model_cfg],
        get_model_config=lambda n: model_cfg,
    )

    import unittest.mock as mock

    with (
        mock.patch.object(factory, "resolve_class", return_value=_FakeModel),
        mock.patch.object(factory, "_apply_local_endpoint_api_key_default"),
        mock.patch.object(factory, "_enable_stream_usage_by_default"),
        mock.patch.object(factory, "_apply_stream_chunk_timeout_default"),
    ):
        token = set_byok_api_key("user-key-xyz")
        try:
            factory.create_chat_model("m", app_config=app_cfg, attach_tracing=False)
        finally:
            reset_byok_api_key(token)

    assert captured.get("api_key") == "user-key-xyz"


# ── Endpoints ────────────────────────────────────────────────────────────


@pytest.fixture()
def client(tmp_path):
    from app.gateway import deps
    from app.gateway.app import create_app
    from deerflow.persistence.engine import close_engine, init_engine

    set_auth_config(AuthConfig(jwt_secret=_TEST_SECRET))
    url = f"sqlite+aiosqlite:///{tmp_path}/byok.db"
    asyncio.run(init_engine("sqlite", url=url, sqlite_dir=str(tmp_path)))
    deps._cached_local_provider = None
    deps._cached_repo = None
    try:
        yield TestClient(create_app())
    finally:
        deps._cached_local_provider = None
        deps._cached_repo = None
        asyncio.run(close_engine())


def _csrf(client: TestClient) -> dict[str, str]:
    token = client.cookies.get("csrf_token")
    return {"X-CSRF-Token": token} if token else {}


def test_byok_status_disabled(client, monkeypatch):
    monkeypatch.delenv("NOVA_BYOK_ENABLED", raising=False)
    client.post("/api/v1/auth/register", json={"email": "u@example.com", "password": _PASSWORD})
    resp = client.get("/api/v1/byok")
    assert resp.status_code == 200
    assert resp.json() == {"enabled": False, "has_key": False, "provider": None}


def test_byok_set_rejected_when_disabled(client, monkeypatch):
    monkeypatch.delenv("NOVA_BYOK_ENABLED", raising=False)
    client.post("/api/v1/auth/register", json={"email": "u@example.com", "password": _PASSWORD})
    resp = client.post(
        "/api/v1/byok",
        json={"provider": "openai", "api_key": "sk-abcdefgh"},
        headers=_csrf(client),
    )
    assert resp.status_code == 403


def test_byok_set_and_status_and_clear(client, byok_env):
    client.post("/api/v1/auth/register", json={"email": "u@example.com", "password": _PASSWORD})

    # Set
    resp = client.post(
        "/api/v1/byok",
        json={"provider": "openai", "api_key": "sk-abcdefgh1234"},
        headers=_csrf(client),
    )
    assert resp.status_code == 200, resp.text

    # Status reflects presence + provider, never the key itself.
    status = client.get("/api/v1/byok").json()
    assert status == {"enabled": True, "has_key": True, "provider": "openai"}

    # Credits report unlimited while a BYOK key is active.
    assert client.get("/api/v1/credits").json()["unlimited"] is True

    # Clear
    resp = client.request("DELETE", "/api/v1/byok", headers=_csrf(client))
    assert resp.status_code == 200
    assert client.get("/api/v1/byok").json()["has_key"] is False


def test_byok_requires_auth(client, byok_env):
    resp = client.get("/api/v1/byok")
    assert resp.status_code == 401


# ── start_run: BYOK bypasses the wall and injects the key ────────────────


@pytest.mark.asyncio
async def test_start_run_byok_bypasses_wall_and_sets_key(monkeypatch):
    """A user with an active BYOK key skips the wall and gets their key set
    into the run context (proven via the short-circuit after set)."""
    from types import SimpleNamespace
    from unittest.mock import AsyncMock, MagicMock

    from app.gateway import services

    thread_store = MagicMock()
    thread_store.check_access = AsyncMock(return_value=True)
    run_ctx = SimpleNamespace(thread_store=thread_store)

    monkeypatch.setattr(services, "get_stream_bridge", lambda r: MagicMock())
    monkeypatch.setattr(services, "get_run_manager", lambda r: MagicMock())
    monkeypatch.setattr(services, "get_run_context", lambda r: run_ctx)
    monkeypatch.setattr(services, "get_trusted_internal_owner_user_id", lambda r: "owner1")

    # Active BYOK key; get_balance must never be consulted (no wall).
    monkeypatch.setattr("app.gateway.byok.has_active_key", AsyncMock(return_value=True))
    monkeypatch.setattr("app.gateway.byok.get_decrypted_key", AsyncMock(return_value="user-key-xyz"))

    async def _no_balance(*_a, **_k):
        raise AssertionError("wall must not run for BYOK users")

    monkeypatch.setattr("app.gateway.credits.get_balance", _no_balance)

    set_keys: list[str | None] = []
    monkeypatch.setattr(
        "deerflow.runtime.byok_context.set_byok_api_key",
        lambda k: set_keys.append(k),
    )

    # Short-circuit right after the credit/BYOK gate.
    def _stop(*_a, **_k):
        raise RuntimeError("reached create_or_reject")

    monkeypatch.setattr(services, "set_current_user", _stop)

    request = SimpleNamespace(state=SimpleNamespace(user=SimpleNamespace(id="u1", system_role="user", plan="free")))
    body = SimpleNamespace(
        on_disconnect="continue",
        context={},
        assistant_id=None,
        metadata={},
        input={},
        config={},
        multitask_strategy="reject",
    )

    with pytest.raises(RuntimeError, match="reached create_or_reject"):
        await services.start_run(body, "t1", request)
    assert set_keys == ["user-key-xyz"]
