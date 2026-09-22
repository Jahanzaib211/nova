"""Probing integrations from the request path must not block the loop."""

from __future__ import annotations

import httpx
import pytest

from deerflow.config.integrations_config import IntegrationsConfig, IntegrationServiceConfig
from deerflow.integrations.registry import build_registry

pytestmark = pytest.mark.anyio


async def test_probe_all_does_no_blocking_io():
    cfg = IntegrationsConfig(
        enabled=True,
        probe_timeout_seconds=1.0,
        services={
            "ollama": IntegrationServiceConfig(kind="llm_gateway", url="http://ollama:11434"),
            "chatwoot": IntegrationServiceConfig(kind="helpdesk", url="http://chatwoot:4800"),
        },
    )
    reg = build_registry(
        cfg,
        transport=httpx.MockTransport(lambda r: httpx.Response(200, json={"models": []})),
        include_extensions=False,
    )
    results = await reg.probe_all(refresh=True)
    assert {r.id for r in results} == {"ollama", "chatwoot"}
