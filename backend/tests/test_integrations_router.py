"""``/api/integrations`` — list, get, probe; disabled section is 404."""

from __future__ import annotations

import httpx
import pytest
from _router_auth_helpers import make_authed_test_app
from fastapi.testclient import TestClient

from app.gateway.routers import integrations
from deerflow.config.integrations_config import IntegrationsConfig, IntegrationServiceConfig
from deerflow.integrations.registry import build_registry

pytestmark = pytest.mark.no_auto_user


def _registry(enabled: bool = True):
    cfg = IntegrationsConfig(
        enabled=enabled,
        probe_timeout_seconds=1.0,
        services={"ollama": IntegrationServiceConfig(kind="llm_gateway", url="http://ollama:11434")},
    )
    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        return httpx.Response(200, json={"models": [{"name": "qwen3:8b"}]})

    return build_registry(cfg, transport=httpx.MockTransport(handler), include_extensions=False), calls


def _client(registry) -> TestClient:
    app = make_authed_test_app()
    app.state.integrations_registry = registry
    app.include_router(integrations.router)
    return TestClient(app)


def test_list_returns_contract_items_and_caches():
    reg, calls = _registry()
    c = _client(reg)
    body = c.get("/api/integrations").json()
    assert body["enabled"] is True
    assert [i["id"] for i in body["integrations"]] == ["ollama"]
    assert body["integrations"][0]["status"] == "healthy"
    assert body["integrations"][0]["capabilities"] == ["qwen3:8b"]
    c.get("/api/integrations")
    assert calls["n"] == 1
    c.get("/api/integrations?refresh=1")
    assert calls["n"] == 2


def test_get_and_probe_one():
    reg, calls = _registry()
    c = _client(reg)
    assert c.get("/api/integrations/ollama").json()["id"] == "ollama"
    n = calls["n"]
    r = c.post("/api/integrations/ollama/probe")
    assert r.status_code == 200
    assert calls["n"] == n + 1
    assert c.get("/api/integrations/nope").status_code == 404
    assert c.post("/api/integrations/nope/probe").status_code == 404


def test_disabled_section_lists_nothing_but_says_so():
    reg, _ = _registry(enabled=False)
    c = _client(reg)
    body = c.get("/api/integrations").json()
    assert body["enabled"] is False
    assert body["integrations"] == []


def test_requires_auth_and_registry():
    from fastapi import FastAPI

    app = FastAPI()
    app.include_router(integrations.router)
    assert TestClient(app).get("/api/integrations").status_code in (401, 403)
