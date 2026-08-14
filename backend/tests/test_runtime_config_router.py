"""Tests for the read-only runtime config endpoint."""

from __future__ import annotations

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.gateway.deps import get_config
from app.gateway.routers import runtime as runtime_router


def _make_app(config) -> FastAPI:
    app = FastAPI()
    app.dependency_overrides[get_config] = lambda: config
    app.include_router(runtime_router.router)
    return app


def _build_config(**overrides):
    """Build a minimal AppConfig-like object for the runtime endpoint."""
    from types import SimpleNamespace

    cfg = SimpleNamespace()
    cfg.summarization = SimpleNamespace(
        enabled=False,
        model_name=None,
        trigger=None,
        keep=SimpleNamespace(type="messages", value=20),
    )
    cfg.subagents = SimpleNamespace(
        timeout_seconds=1800,
        max_turns=None,
        custom_agents={},
    )
    cfg.guardrails = SimpleNamespace(
        enabled=False,
        fail_closed=True,
        passport=None,
        provider=None,
    )
    for k, v in overrides.items():
        setattr(cfg, k, v)
    return cfg


def test_returns_defaults_when_config_minimal():
    cfg = _build_config()
    client = TestClient(_make_app(cfg))
    resp = client.get("/api/runtime/config")
    assert resp.status_code == 200
    body = resp.json()
    assert body["summarization"]["enabled"] is False
    assert body["summarization"]["keep_type"] == "messages"
    assert body["summarization"]["keep_value"] == 20
    assert body["subagents"]["default_timeout_seconds"] == 1800
    assert body["subagents"]["custom_agents_count"] == 0
    assert body["guardrails"]["enabled"] is False
    assert body["guardrails"]["fail_closed"] is True


def test_returns_summarization_trigger_and_model():
    from types import SimpleNamespace

    cfg = _build_config(
        summarization=SimpleNamespace(
            enabled=True,
            model_name="gpt-4o-mini",
            trigger=SimpleNamespace(type="tokens", value=4000),
            keep=SimpleNamespace(type="fraction", value=0.3),
        )
    )
    client = TestClient(_make_app(cfg))
    resp = client.get("/api/runtime/config")
    body = resp.json()
    assert body["summarization"]["enabled"] is True
    assert body["summarization"]["model_name"] == "gpt-4o-mini"
    assert body["summarization"]["trigger_type"] == "tokens"
    assert body["summarization"]["trigger_value"] == 4000
    assert body["summarization"]["keep_type"] == "fraction"
    assert body["summarization"]["keep_value"] == 0.3


def test_returns_subagents_custom_count():
    from types import SimpleNamespace

    cfg = _build_config(
        subagents=SimpleNamespace(
            timeout_seconds=900,
            max_turns=50,
            custom_agents={"researcher": object(), "coder": object()},
        )
    )
    client = TestClient(_make_app(cfg))
    body = client.get("/api/runtime/config").json()
    assert body["subagents"]["default_timeout_seconds"] == 900
    assert body["subagents"]["max_turns"] == 50
    assert body["subagents"]["custom_agents_count"] == 2


def test_returns_guardrails_provider_class():
    from types import SimpleNamespace

    cfg = _build_config(
        guardrails=SimpleNamespace(
            enabled=True,
            fail_closed=False,
            passport="oap:abc123",
            provider=SimpleNamespace(
                use="deerflow.guardrails.builtin:AllowlistProvider",
                config={},
            ),
        )
    )
    client = TestClient(_make_app(cfg))
    body = client.get("/api/runtime/config").json()
    assert body["guardrails"]["enabled"] is True
    assert body["guardrails"]["fail_closed"] is False
    assert body["guardrails"]["passport"] == "oap:abc123"
    assert body["guardrails"]["provider_class"] == "deerflow.guardrails.builtin:AllowlistProvider"


def test_handles_missing_subagent_fields_gracefully():
    from types import SimpleNamespace

    cfg = _build_config(
        subagents=SimpleNamespace(timeout_seconds=600)
    )
    client = TestClient(_make_app(cfg))
    body = client.get("/api/runtime/config").json()
    assert body["subagents"]["default_timeout_seconds"] == 600
    assert body["subagents"]["custom_agents_count"] == 0


def test_handles_none_summarization():
    cfg = _build_config(summarization=None)
    client = TestClient(_make_app(cfg))
    body = client.get("/api/runtime/config").json()
    assert body["summarization"]["enabled"] is False
