"""One MCP tool counter: ``deerflow.mcp.cache.tool_counts_by_server``.

The integrations adapter counted by ``<server>_`` name prefix (what
``tool_name_prefix=True`` produces) while ``mcp.servers`` counted by
``metadata["mcp_server"]``, which nothing sets — so the capability reported
0 tools for every server while the integrations page reported the truth.
"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest

from deerflow.mcp import cache


@pytest.fixture()
def loaded(monkeypatch):
    tools = [SimpleNamespace(name=n, description="") for n in ("github_create_issue", "github_list_prs", "git_status", "fs_read", "orphan")]
    monkeypatch.setattr(cache, "_mcp_tools_cache", tools)
    monkeypatch.setattr(cache, "_cache_initialized", True)
    return tools


def test_counts_by_longest_matching_prefix(loaded):
    assert cache.tool_counts_by_server(["git", "github", "fs", "idle"]) == {"github": 2, "git": 1, "fs": 1}
    assert cache.server_for_tool("github_create_issue", ["git", "github"]) == "github"
    assert cache.server_for_tool("orphan", ["git", "github"]) is None


def test_counting_never_triggers_loading(monkeypatch):
    monkeypatch.setattr(cache, "_mcp_tools_cache", None)
    monkeypatch.setattr(cache, "_cache_initialized", False)
    monkeypatch.setattr(cache, "initialize_mcp_tools", lambda: pytest.fail("a count must not connect to MCP servers"))
    assert cache.tool_counts_by_server(["github"]) == {}
    assert cache.mcp_tools_loaded() is False


def test_capability_and_integrations_adapter_agree(loaded, monkeypatch):
    from deerflow.capabilities.modules import mcp as mcp_module
    from deerflow.capabilities.types import OpContext
    from deerflow.integrations import adapters

    servers = {"github": SimpleNamespace(enabled=True, type="stdio", command="npx", url=None, description=""), "git": SimpleNamespace(enabled=True, type="stdio", command="uvx", url=None, description="")}
    monkeypatch.setattr(mcp_module, "_servers", lambda: servers)
    monkeypatch.setattr(adapters, "_mcp_servers", lambda: {name: {"enabled": True, "type": "stdio"} for name in servers})

    listed = asyncio.run(mcp_module._list(OpContext(user_id="u"), mcp_module.Empty()))
    by_capability = {item["name"]: item["tool_count"] for item in listed.items}
    by_adapter = {r.display_name: r.detail for r in adapters.mcp_adapter()}
    assert by_capability == {"git": 1, "github": 2}
    assert by_adapter == {"git": "1 tools", "github": "2 tools"}
