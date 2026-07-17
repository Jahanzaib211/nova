"""Regression anchors: the custom-agent router must not block the event loop.

``app.gateway.routers.agents.create_agent_endpoint`` and ``delete_agent`` are
async route handlers that resolve the agent directory (``Paths.base_dir`` calls
``Path.resolve``), probe it (``Path.exists``), and create/remove it (``mkdir``,
config/SOUL writes, ``shutil.rmtree``) — all blocking IO. Both offload that work
via ``asyncio.to_thread``; if any of it regresses back onto the event loop, the
strict Blockbuster gate raises ``BlockingError`` and these tests fail.

Imports live at module scope so the one-time FastAPI app construction (which
reads files while building OpenAPI schemas) happens at collection time, not on
the event loop under test. Test-side path resolution is itself offloaded with
``asyncio.to_thread`` (matching ``test_uploads_middleware``) so only the
handlers' own filesystem access is exercised on the loop.
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from types import SimpleNamespace

import pytest

from app.gateway.routers.agents import AgentCreateRequest, create_agent_endpoint, delete_agent
from deerflow.config.agents_api_config import load_agents_api_config_from_dict
from deerflow.config.paths import get_paths
from deerflow.runtime.user_context import get_effective_user_id

pytestmark = pytest.mark.asyncio

DEFAULT_AGENT_USER_ID = "test-user-autouse"


def _make_auth_stub() -> SimpleNamespace:
    return SimpleNamespace(state=SimpleNamespace(state=SimpleNamespace()), cookies={}, _deerflow_test_bypass_auth=True)


async def test_create_agent_does_not_block_event_loop(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("DEER_FLOW_HOME", str(tmp_path))
    monkeypatch.setattr("deerflow.config.paths._paths", None)
    load_agents_api_config_from_dict({"enabled": True})
    try:
        response = await create_agent_endpoint(
            request=_make_auth_stub(),
            body=AgentCreateRequest(name="loop-make-agent", soul="You are a test agent."),
        )
        assert response is not None

        # The bypass preserves the contextvar user (test-user-autouse from autouse fixture)
        agent_dir = await asyncio.to_thread(get_paths().user_agent_dir, DEFAULT_AGENT_USER_ID, "loop-make-agent")
        assert await asyncio.to_thread((agent_dir / "config.yaml").exists)
    finally:
        load_agents_api_config_from_dict({})


async def test_delete_agent_does_not_block_event_loop(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("DEER_FLOW_HOME", str(tmp_path))
    monkeypatch.setattr("deerflow.config.paths._paths", None)
    load_agents_api_config_from_dict({"enabled": True})
    try:
        # The bypass preserves the contextvar user (test-user-autouse from autouse fixture)
        agent_dir = await asyncio.to_thread(get_paths().user_agent_dir, DEFAULT_AGENT_USER_ID, "loop-test-agent")
        await asyncio.to_thread(agent_dir.mkdir, parents=True, exist_ok=True)
        await asyncio.to_thread((agent_dir / "config.yaml").write_text, "name: loop-test-agent\n", encoding="utf-8")

        await delete_agent("loop-test-agent", request=_make_auth_stub())

        assert not await asyncio.to_thread(agent_dir.exists)
    finally:
        load_agents_api_config_from_dict({})
