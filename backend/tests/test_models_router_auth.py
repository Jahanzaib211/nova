"""B3 Gate 5 — auth-gating + ownership enforcement on the models API.

Mirrors Gate 4 (agents):
- write endpoints (POST /api/models, PUT/DELETE /api/models/{name}) require
  authentication; the owner is resolved server-side.
- ownership metadata lives in model_configs; runtime_models.yaml stays the
  authoritative store (share-by-default reads are unchanged).
- enforcement: a row owned by another user is 403 for non-admins; NULL-owner
  (legacy/backfilled) rows stay editable by any authenticated user; admins
  manage everything.
- memory-mode (no DB): writes keep working, ownership is best-effort.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from _router_auth_helpers import make_authed_test_app
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.gateway.auth.models import User
from app.gateway.routers.models import router as models_router

LLAMA_ENTRY = {
    "name": "local-llm",
    "model": "local-model-gguf",
    "use": "langchain_openai:ChatOpenAI",
    "base_url": "http://127.0.0.1:9000/v1",
    "api_key": "sk-local",
}


def _user(user_id, role="user"):
    return User(email=f"{role}@example.com", password_hash="x", system_role=role, id=user_id)


@pytest.fixture(autouse=True)
def _isolate_app_config_singleton():
    from deerflow.config.app_config import reset_app_config

    reset_app_config()
    yield
    reset_app_config()


@pytest.fixture
def config_env(tmp_path, monkeypatch):
    config_path = tmp_path / "config.yaml"
    config_path.write_text(
        "sandbox:\n  use: deerflow.sandbox.local.provider:LocalSandboxProvider\nmodels:\n  - name: config-model\n    use: langchain_openai:ChatOpenAI\n    model: gpt-4o-mini\n    api_key: fake-key\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("DEER_FLOW_CONFIG_PATH", str(config_path))
    monkeypatch.delenv("DEER_FLOW_RUNTIME_MODELS_PATH", raising=False)
    monkeypatch.setenv("DEER_FLOW_HOME", str(tmp_path / "home"))
    return tmp_path


class _FakeRepo:
    """In-memory stand-in for ModelRepository ownership metadata."""

    def __init__(self):
        self.rows: dict[str, SimpleNamespace] = {}

    async def get_by_name(self, name):
        return self.rows.get(name)

    async def upsert_owner(self, owner_id, name, model="", model_use=""):
        row = self.rows.get(name) or SimpleNamespace(owner_id=owner_id, name=name)
        row.owner_id = owner_id
        self.rows[name] = row
        return row

    async def delete_by_name(self, name):
        return self.rows.pop(name, None) is not None


@pytest.fixture
def fake_repo(monkeypatch):
    from app.gateway.routers import models as models_module

    repo = _FakeRepo()
    monkeypatch.setattr(models_module, "get_model_repo", lambda: repo)
    return repo


def _client(user):
    app = make_authed_test_app(user_factory=lambda: user)
    app.include_router(models_router)
    return TestClient(app)


class TestAuthRequired:
    def test_writes_401_without_auth(self, config_env, fake_repo):
        app = FastAPI()
        app.include_router(models_router)
        with TestClient(app) as client:
            assert client.post("/api/models", json=LLAMA_ENTRY).status_code == 401
            assert client.put("/api/models/x", json=LLAMA_ENTRY).status_code == 401
            assert client.delete("/api/models/x").status_code == 401

    def test_reads_stay_public(self, config_env, fake_repo):
        app = FastAPI()
        app.include_router(models_router)
        with TestClient(app) as client:
            assert client.get("/api/models").status_code == 200


class TestOwnership:
    def test_create_records_owner(self, config_env, fake_repo):
        user_id = uuid4()
        with _client(_user(user_id)) as client:
            assert client.post("/api/models", json=LLAMA_ENTRY).status_code == 201
        assert fake_repo.rows["local-llm"].owner_id == str(user_id)

    def test_update_by_owner_succeeds(self, config_env, fake_repo):
        user_id = uuid4()
        with _client(_user(user_id)) as client:
            client.post("/api/models", json=LLAMA_ENTRY)
            update = {**LLAMA_ENTRY, "display_name": "Mine"}
            assert client.put("/api/models/local-llm", json=update).status_code == 200

    def test_update_by_other_user_is_403(self, config_env, fake_repo):
        owner, intruder = uuid4(), uuid4()
        with _client(_user(owner)) as client:
            client.post("/api/models", json=LLAMA_ENTRY)
        with _client(_user(intruder)) as client:
            response = client.put("/api/models/local-llm", json={**LLAMA_ENTRY, "display_name": "Stolen"})
        assert response.status_code == 403

    def test_delete_by_other_user_is_403_and_owner_can_delete(self, config_env, fake_repo):
        owner, intruder = uuid4(), uuid4()
        with _client(_user(owner)) as client:
            client.post("/api/models", json=LLAMA_ENTRY)
        with _client(_user(intruder)) as client:
            assert client.delete("/api/models/local-llm").status_code == 403
        with _client(_user(owner)) as client:
            assert client.delete("/api/models/local-llm").status_code == 200
        assert "local-llm" not in fake_repo.rows

    def test_admin_can_modify_any(self, config_env, fake_repo):
        owner, admin = uuid4(), uuid4()
        with _client(_user(owner)) as client:
            client.post("/api/models", json=LLAMA_ENTRY)
        with _client(_user(admin, role="admin")) as client:
            assert client.put("/api/models/local-llm", json={**LLAMA_ENTRY, "display_name": "Admin"}).status_code == 200
            assert client.delete("/api/models/local-llm").status_code == 200

    def test_null_owner_legacy_row_editable_by_anyone(self, config_env, fake_repo):
        creator, editor = uuid4(), uuid4()
        with _client(_user(creator)) as client:
            client.post("/api/models", json=LLAMA_ENTRY)
        fake_repo.rows["local-llm"].owner_id = None  # simulate backfilled legacy row
        with _client(_user(editor)) as client:
            response = client.put("/api/models/local-llm", json={**LLAMA_ENTRY, "display_name": "Shared"})
        assert response.status_code == 200
        # editing a shared row must NOT capture ownership
        assert fake_repo.rows["local-llm"].owner_id is None

    def test_yaml_model_without_metadata_row_stays_editable(self, config_env, fake_repo):
        editor = uuid4()
        with _client(_user(editor)) as client:
            client.post("/api/models", json=LLAMA_ENTRY)
        del fake_repo.rows["local-llm"]  # YAML row predates metadata
        with _client(_user(editor)) as client:
            assert client.put("/api/models/local-llm", json={**LLAMA_ENTRY, "display_name": "Legacy"}).status_code == 200
        # legacy rows are recorded as shared (NULL owner), not claimed
        assert fake_repo.rows["local-llm"].owner_id is None


class TestMemoryMode:
    def test_writes_work_without_db(self, config_env, monkeypatch):
        from app.gateway.routers import models as models_module

        def _raise():
            raise RuntimeError("memory-mode")

        monkeypatch.setattr(models_module, "get_model_repo", _raise)
        with _client(_user(uuid4())) as client:
            assert client.post("/api/models", json=LLAMA_ENTRY).status_code == 201
            assert client.put("/api/models/local-llm", json={**LLAMA_ENTRY, "display_name": "M"}).status_code == 200
            assert client.delete("/api/models/local-llm").status_code == 200


class TestRepositoryHelpers:
    @pytest.mark.anyio
    async def test_upsert_owner_and_get_by_name_round_trip(self):
        from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

        from app.gateway.repositories.model_repository import ModelRepository
        from deerflow.persistence.base import Base

        engine = create_async_engine("sqlite+aiosqlite://")
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        repo = ModelRepository(async_sessionmaker(engine, expire_on_commit=False))

        await repo.upsert_owner(owner_id="u1", name="m1", model="gpt", model_use="langchain_openai:ChatOpenAI")
        row = await repo.get_by_name("m1")
        assert row is not None and row.owner_id == "u1"

        await repo.upsert_owner(owner_id=None, name="m1", model="gpt", model_use="langchain_openai:ChatOpenAI")
        row = await repo.get_by_name("m1")
        assert row.owner_id is None

        assert await repo.delete_by_name("m1") is True
        assert await repo.get_by_name("m1") is None
        assert await repo.delete_by_name("m1") is False
        await engine.dispose()
