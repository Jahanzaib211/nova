"""``deerflow.integrations`` — config, clients, registry and probe caching.

All HTTP goes through ``httpx.MockTransport``: nothing here touches the
network, and every client is exercised against the shape its real service
returns (Ollama ``/api/tags``, LiteLLM ``/v1/models``, Mailcow
``/api/v1/get/status/version`` …).
"""

from __future__ import annotations

import json

import httpx
import pytest

from deerflow.config.integrations_config import IntegrationsConfig, IntegrationServiceConfig
from deerflow.integrations.health import IntegrationKind, IntegrationStatus
from deerflow.integrations.registry import IntegrationRegistry, build_registry

pytestmark = pytest.mark.anyio


def _transport(routes: dict[str, tuple[int, object]]) -> httpx.MockTransport:
    def handler(request: httpx.Request) -> httpx.Response:
        key = request.url.path
        if key not in routes:
            return httpx.Response(404, text="no route")
        status, body = routes[key]
        if isinstance(body, (dict, list)):
            return httpx.Response(status, json=body)
        return httpx.Response(status, text=str(body))

    return httpx.MockTransport(handler)


def _config(**services: dict) -> IntegrationsConfig:
    return IntegrationsConfig(
        enabled=True,
        probe_cache_seconds=20,
        probe_timeout_seconds=1.0,
        services={k: IntegrationServiceConfig(**v) for k, v in services.items()},
    )


# ----------------------------------------------------------------- config


def test_config_defaults_are_off_and_env_key_names_never_resolve_at_load(monkeypatch):
    cfg = IntegrationsConfig()
    assert cfg.enabled is False
    assert cfg.services == {}
    svc = IntegrationServiceConfig(kind="mail", url="http://x", api_key_env="NOVA_TEST_MISSING_KEY")
    monkeypatch.delenv("NOVA_TEST_MISSING_KEY", raising=False)
    # A missing env var is a runtime condition (probe says so), never a config
    # load failure — unlike `$VAR` values, which AppConfig resolves eagerly.
    assert svc.resolve_api_key() is None
    monkeypatch.setenv("NOVA_TEST_MISSING_KEY", "sekrit")
    assert svc.resolve_api_key() == "sekrit"


def test_config_rejects_unknown_kind():
    with pytest.raises(ValueError):
        IntegrationServiceConfig(kind="spaceship", url="http://x")


# ----------------------------------------------------------------- clients


async def test_ollama_reports_models_as_capabilities():
    reg = build_registry(
        _config(ollama={"kind": "llm_gateway", "url": "http://ollama:11434"}),
        transport=_transport({"/api/tags": (200, {"models": [{"name": "qwen3:8b"}, {"name": "nomic-embed"}]})}),
    )
    result = await reg.probe("ollama", refresh=True)
    assert result.status is IntegrationStatus.HEALTHY
    assert result.kind is IntegrationKind.LLM_GATEWAY
    assert result.capabilities == ["qwen3:8b", "nomic-embed"]
    assert result.latency_ms is not None and result.latency_ms >= 0
    assert result.checked_at is not None


async def test_litellm_lists_models_and_flags_unauthorised_as_degraded():
    reg = build_registry(
        _config(litellm={"kind": "llm_gateway", "url": "http://litellm:4000"}),
        transport=_transport({"/health/liveliness": (200, "alive"), "/v1/models": (401, {"error": "no key"})}),
    )
    result = await reg.probe("litellm", refresh=True)
    assert result.status is IntegrationStatus.DEGRADED
    assert "401" in (result.detail or "")


async def test_mailcow_without_key_is_degraded_not_down(monkeypatch):
    monkeypatch.delenv("MAILCOW_API_KEY", raising=False)
    reg = build_registry(
        _config(mailcow={"kind": "mail", "url": "http://mailcow:8080", "api_key_env": "MAILCOW_API_KEY"}),
        transport=_transport({"/api/v1/get/status/version": (401, {"msg": "authentication failed"})}),
    )
    result = await reg.probe("mailcow", refresh=True)
    assert result.status is IntegrationStatus.DEGRADED
    assert "MAILCOW_API_KEY" in (result.detail or "")


async def test_mailcow_with_key_reports_version_and_capabilities(monkeypatch):
    monkeypatch.setenv("MAILCOW_API_KEY", "k")
    seen: dict[str, str] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["key"] = request.headers.get("X-API-Key", "")
        return httpx.Response(200, json={"version": "2026-08"})

    reg = build_registry(
        _config(mailcow={"kind": "mail", "url": "http://mailcow:8080", "api_key_env": "MAILCOW_API_KEY"}),
        transport=httpx.MockTransport(handler),
    )
    result = await reg.probe("mailcow", refresh=True)
    assert seen["key"] == "k"
    assert result.status is IntegrationStatus.HEALTHY
    assert "2026-08" in (result.detail or "")
    assert {"mailbox", "alias", "dkim"} <= set(result.capabilities)


async def test_connection_error_is_down_with_the_reason():
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("connection refused")

    reg = build_registry(
        _config(chatwoot={"kind": "helpdesk", "url": "http://chatwoot:4800"}),
        transport=httpx.MockTransport(handler),
    )
    result = await reg.probe("chatwoot", refresh=True)
    assert result.status is IntegrationStatus.DOWN
    assert "refused" in (result.detail or "")


async def test_disabled_service_is_reported_not_probed():
    calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(200, json={})

    reg = build_registry(
        _config(twenty={"kind": "crm", "url": "http://twenty:3008", "enabled": False}),
        transport=httpx.MockTransport(handler),
    )
    result = await reg.probe("twenty", refresh=True)
    assert result.status is IntegrationStatus.DISABLED
    assert calls == 0


# ----------------------------------------------------------------- registry


async def test_probe_all_is_cached_until_refresh_or_expiry(monkeypatch):
    calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(200, json={"models": []})

    reg = build_registry(
        _config(ollama={"kind": "llm_gateway", "url": "http://ollama:11434"}),
        transport=httpx.MockTransport(handler),
        include_extensions=False,
    )
    first = await reg.probe_all()
    again = await reg.probe_all()
    assert calls == 1
    assert [r.to_dict() for r in first] == [r.to_dict() for r in again]
    await reg.probe_all(refresh=True)
    assert calls == 2
    # Expiry: pretend the clock moved past the cache window.
    monkeypatch.setattr(reg, "_now", lambda: reg._last_probe_at + 999)
    await reg.probe_all()
    assert calls == 3


async def test_probe_unknown_id_raises_key_error():
    reg = build_registry(_config(), include_extensions=False)
    with pytest.raises(KeyError):
        await reg.probe("nope")


async def test_results_match_the_contract_item_schema():
    from pathlib import Path

    contract = json.loads((Path(__file__).resolve().parents[2] / "contracts" / "integrations_health_contract.json").read_text())
    required = set(contract["item_schema"]["required"])
    allowed = set(contract["item_schema"]["properties"])
    reg = build_registry(
        _config(ollama={"kind": "llm_gateway", "url": "http://ollama:11434"}),
        transport=_transport({"/api/tags": (200, {"models": []})}),
        include_extensions=False,
    )
    for result in await reg.probe_all(refresh=True):
        d = result.to_dict()
        assert required <= set(d)
        assert set(d) <= allowed
        assert d["status"] in contract["statuses"]
        assert d["kind"] in contract["kinds"]


async def test_extension_adapters_list_mcp_servers_skills_and_acp_agents(monkeypatch, tmp_path):
    from deerflow.config.acp_config import ACPAgentConfig
    from deerflow.integrations import adapters

    monkeypatch.setattr(adapters, "_mcp_servers", lambda: {"github": {"enabled": True}, "old": {"enabled": False}})
    monkeypatch.setattr(adapters, "_mcp_tool_counts", lambda: ({"github": 12}, True))
    monkeypatch.setattr(adapters, "_skills", lambda: [("pdf", True), ("docx", False)])
    monkeypatch.setattr(
        adapters,
        "_acp_agents",
        lambda: {"claude_code": ACPAgentConfig(command="definitely-not-on-path-xyz", description="d")},
    )
    reg = IntegrationRegistry(_config(), clients=[], adapters=adapters.default_adapters())
    results = {r.id: r for r in await reg.probe_all(refresh=True)}
    assert results["mcp:github"].status is IntegrationStatus.HEALTHY
    assert results["mcp:github"].detail == "12 tools"
    assert results["mcp:old"].status is IntegrationStatus.DISABLED
    assert results["skill:pdf"].status is IntegrationStatus.HEALTHY
    assert results["skill:docx"].status is IntegrationStatus.DISABLED
    assert results["acp:claude_code"].status is IntegrationStatus.DOWN
    assert "not found" in (results["acp:claude_code"].detail or "")
