"""Tests for the runtime model store and /api/models CRUD.

Runtime-added models live in ``runtime_models.yaml`` next to ``config.yaml``
(never written into config.yaml itself), merge into ``AppConfig.models`` at
load, and participate in the mtime/signature hot-reload — so a POST becomes
visible to the next ``get_app_config()`` call without a restart.
"""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.gateway.routers.models import router as models_router
from deerflow.config.app_config import get_app_config, reset_app_config
from deerflow.config.runtime_models import (
    load_runtime_model_dicts,
    runtime_models_path,
    save_runtime_model_dicts,
)


@pytest.fixture(autouse=True)
def _isolate_app_config_singleton():
    reset_app_config()
    yield
    reset_app_config()


def _write_config_yaml(path: Path) -> None:
    path.write_text(
        """
sandbox:
  use: deerflow.sandbox.local.provider:LocalSandboxProvider
models:
  - name: config-model
    use: langchain_openai:ChatOpenAI
    model: gpt-4o-mini
    api_key: fake-key
""".strip()
        + "\n",
        encoding="utf-8",
    )


@pytest.fixture
def config_env(tmp_path, monkeypatch):
    config_path = tmp_path / "config.yaml"
    _write_config_yaml(config_path)
    monkeypatch.setenv("DEER_FLOW_CONFIG_PATH", str(config_path))
    monkeypatch.delenv("DEER_FLOW_RUNTIME_MODELS_PATH", raising=False)
    monkeypatch.delenv("DEER_FLOW_HOME", raising=False)
    return tmp_path


@pytest.fixture
def client(config_env):
    app = FastAPI()
    app.include_router(models_router)
    return TestClient(app)


LLAMA_ENTRY = {
    "name": "ornith-local",
    "model": "ornith",
    "use": "langchain_openai:ChatOpenAI",
    "base_url": "http://127.0.0.1:8081/v1",
    "api_key": "not-needed",
    "display_name": "Ornith (local)",
}


def test_runtime_store_roundtrip(config_env):
    path = runtime_models_path()
    assert path == config_env / "runtime_models.yaml"
    assert load_runtime_model_dicts(path) == []
    save_runtime_model_dicts([LLAMA_ENTRY], path)
    assert load_runtime_model_dicts(path) == [LLAMA_ENTRY]


def test_runtime_models_merge_into_app_config(config_env):
    save_runtime_model_dicts([LLAMA_ENTRY])
    config = get_app_config()
    names = [m.name for m in config.models]
    assert names == ["config-model", "ornith-local"]


def test_config_yaml_wins_on_name_collision(config_env):
    save_runtime_model_dicts([{**LLAMA_ENTRY, "name": "config-model", "model": "shadow"}])
    config = get_app_config()
    assert [m.name for m in config.models] == ["config-model"]
    assert config.get_model_config("config-model").model == "gpt-4o-mini"


def test_hot_reload_after_runtime_store_write(config_env):
    assert [m.name for m in get_app_config().models] == ["config-model"]
    # A later write to the runtime store alone must trigger the signature reload.
    save_runtime_model_dicts([LLAMA_ENTRY])
    assert [m.name for m in get_app_config().models] == ["config-model", "ornith-local"]


def test_post_creates_runtime_model(client, config_env):
    res = client.post("/api/models", json=LLAMA_ENTRY)
    assert res.status_code == 201, res.text
    body = res.json()
    assert body["ok"] is True
    assert body["model"]["source"] == "runtime"
    assert body["model"]["base_url"] == "http://127.0.0.1:8081/v1"
    assert body["model"]["has_api_key"] is True
    assert "api_key" not in body["model"]

    listing = client.get("/api/models").json()
    assert [m["name"] for m in listing["models"]] == ["config-model", "ornith-local"]
    sources = {m["name"]: m["source"] for m in listing["models"]}
    assert sources == {"config-model": "config", "ornith-local": "runtime"}


def test_post_duplicate_name_conflicts(client):
    assert client.post("/api/models", json=LLAMA_ENTRY).status_code == 201
    assert client.post("/api/models", json=LLAMA_ENTRY).status_code == 409
    config_clash = {**LLAMA_ENTRY, "name": "config-model"}
    assert client.post("/api/models", json=config_clash).status_code == 409


def test_put_updates_and_preserves_api_key_when_omitted(client, config_env):
    client.post("/api/models", json=LLAMA_ENTRY)
    update = {k: v for k, v in LLAMA_ENTRY.items() if k != "api_key"}
    update["display_name"] = "Ornith v2"
    res = client.put("/api/models/ornith-local", json=update)
    assert res.status_code == 200, res.text
    assert res.json()["model"]["display_name"] == "Ornith v2"

    stored = load_runtime_model_dicts(runtime_models_path())
    assert stored[0]["api_key"] == "not-needed"


def test_put_config_model_is_forbidden(client):
    res = client.put("/api/models/config-model", json={**LLAMA_ENTRY, "name": "config-model"})
    assert res.status_code == 403


def test_delete_runtime_model(client):
    client.post("/api/models", json=LLAMA_ENTRY)
    assert client.delete("/api/models/ornith-local").status_code == 200
    names = [m["name"] for m in client.get("/api/models").json()["models"]]
    assert names == ["config-model"]


def test_delete_config_model_forbidden_and_unknown_404(client):
    assert client.delete("/api/models/config-model").status_code == 403
    assert client.delete("/api/models/nope").status_code == 404


def test_invalid_runtime_entries_are_skipped_not_fatal(config_env):
    path = runtime_models_path()
    path.write_text(yaml.safe_dump({"models": [{"display_name": "missing required fields"}, LLAMA_ENTRY]}), encoding="utf-8")
    config = get_app_config()
    assert [m.name for m in config.models] == ["config-model", "ornith-local"]


def test_env_override_for_store_path(tmp_path, monkeypatch, config_env):
    override = tmp_path / "elsewhere" / "models.yaml"
    monkeypatch.setenv("DEER_FLOW_RUNTIME_MODELS_PATH", str(override))
    save_runtime_model_dicts([LLAMA_ENTRY])
    assert override.exists()
    assert load_runtime_model_dicts() == [LLAMA_ENTRY]
