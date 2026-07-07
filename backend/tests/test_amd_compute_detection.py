"""Tests for AMD-compute detection and the /api/models/amd-usage endpoint.

Detection is deliberately conservative: only Fireworks endpoints (verified
AMD-hosted) are auto-claimed; self-hosted endpoints must opt in via an explicit
``amd_compute`` label. This keeps the "runs on AMD" signal honest.
"""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.gateway.routers.models import detect_amd_compute
from app.gateway.routers.models import router as models_router
from deerflow.config.app_config import reset_app_config
from deerflow.config.model_config import ModelConfig


@pytest.fixture(autouse=True)
def _isolate_app_config_singleton():
    reset_app_config()
    yield
    reset_app_config()


def _model(**extra) -> ModelConfig:
    base = {"name": "m", "use": "langchain_openai:ChatOpenAI", "model": "x"}
    base.update(extra)
    return ModelConfig.model_validate(base)


def test_fireworks_endpoint_is_auto_detected():
    m = _model(base_url="https://api.fireworks.ai/inference/v1")
    assert detect_amd_compute(m) == "AMD Instinct MI300X (Fireworks)"


def test_explicit_label_wins():
    m = _model(amd_compute="AMD Instinct MI300X (vLLM/ROCm)")
    assert detect_amd_compute(m) == "AMD Instinct MI300X (vLLM/ROCm)"


def test_explicit_true_gives_generic_label():
    m = _model(amd_compute=True)
    assert detect_amd_compute(m) == "AMD Instinct"


def test_plain_vllm_is_not_auto_claimed():
    # vLLM also runs on non-AMD hardware — never assume AMD without opt-in.
    m = _model(use="deerflow.models.vllm_provider:VllmChatModel", base_url="http://10.0.0.5:8000/v1")
    assert detect_amd_compute(m) is None


def test_non_amd_endpoint_returns_none():
    m = _model(base_url="https://api.openai.com/v1")
    assert detect_amd_compute(m) is None


def _write_config(path: Path, models: list[dict]) -> None:
    path.write_text(
        yaml.safe_dump(
            {
                "sandbox": {"use": "deerflow.sandbox.local:LocalSandboxProvider"},
                "models": models,
            }
        ),
        encoding="utf-8",
    )


@pytest.fixture
def client(tmp_path, monkeypatch):
    config_path = tmp_path / "config.yaml"
    _write_config(
        config_path,
        [
            {"name": "plain", "use": "langchain_openai:ChatOpenAI", "model": "gpt"},
            {
                "name": "fw",
                "use": "langchain_openai:ChatOpenAI",
                "model": "accounts/fireworks/models/llama-v4-maverick",
                "base_url": "https://api.fireworks.ai/inference/v1",
            },
            {
                "name": "amd-vllm",
                "use": "deerflow.models.vllm_provider:VllmChatModel",
                "model": "google/gemma-3-27b-it",
                "base_url": "http://10.0.0.5:8000/v1",
                "amd_compute": "AMD Instinct MI300X (vLLM/ROCm)",
            },
        ],
    )
    monkeypatch.setenv("DEER_FLOW_CONFIG_PATH", str(config_path))
    monkeypatch.delenv("DEER_FLOW_RUNTIME_MODELS_PATH", raising=False)
    monkeypatch.delenv("DEER_FLOW_HOME", raising=False)
    app = FastAPI()
    app.include_router(models_router)
    return TestClient(app)


def test_amd_usage_endpoint_reports_backed_models(client):
    res = client.get("/api/models/amd-usage")
    assert res.status_code == 200
    body = res.json()
    assert body["amd_backed"] is True
    assert body["count"] == 2
    names = {m["name"] for m in body["models"]}
    assert names == {"fw", "amd-vllm"}
    assert "AMD compute" in body["summary"]


def test_amd_usage_route_not_shadowed_by_model_name(client):
    # /models/amd-usage must resolve to the summary endpoint, not get_model("amd-usage").
    res = client.get("/api/models/amd-usage")
    assert res.status_code == 200
    assert "summary" in res.json()


def test_list_models_exposes_amd_compute_field(client):
    res = client.get("/api/models")
    assert res.status_code == 200
    by_name = {m["name"]: m for m in res.json()["models"]}
    assert by_name["plain"]["amd_compute"] is None
    assert by_name["fw"]["amd_compute"] == "AMD Instinct MI300X (Fireworks)"
    assert by_name["amd-vllm"]["amd_compute"] == "AMD Instinct MI300X (vLLM/ROCm)"
