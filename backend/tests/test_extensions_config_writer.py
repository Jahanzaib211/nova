"""One raw-merge writer for ``extensions_config.json``.

``skills.toggle`` / ``mcp.toggle`` (and ``PUT /api/skills/{name}``) used to
rebuild the file from the *loaded* config, which has every ``$VAR``
resolved — so a toggle wrote plaintext secrets to disk, blanked variables
unset in the gateway's environment and dropped top-level keys such as
``mcpInterceptors``. Every writer now edits the raw on-disk JSON.
"""

from __future__ import annotations

import asyncio
import json

import pytest

from deerflow.config.extensions_config import reset_extensions_config, set_mcp_server_enabled, set_skill_enabled, update_extensions_config_file

ORIGINAL = {
    "mcpServers": {
        "github": {"enabled": True, "type": "stdio", "command": "npx", "env": {"GITHUB_TOKEN": "$GITHUB_TOKEN", "OPTIONAL": "$NOVA_TEST_SURELY_UNSET"}},
    },
    "skills": {"deep-research": {"enabled": True}},
    "mcpInterceptors": ["pkg.module:interceptor"],
}


@pytest.fixture()
def config_file(tmp_path, monkeypatch):
    path = tmp_path / "extensions_config.json"
    path.write_text(json.dumps(ORIGINAL), encoding="utf-8")
    monkeypatch.setenv("DEER_FLOW_EXTENSIONS_CONFIG_PATH", str(path))
    monkeypatch.setenv("GITHUB_TOKEN", "ghp_plaintext_secret")
    reset_extensions_config()
    yield path
    reset_extensions_config()


def _assert_untouched_except(path, *, skills=None, servers=None):
    text = path.read_text(encoding="utf-8")
    written = json.loads(text)
    assert "ghp_plaintext_secret" not in text
    assert written["mcpInterceptors"] == ORIGINAL["mcpInterceptors"]
    assert written["skills"] == (skills if skills is not None else ORIGINAL["skills"])
    assert written["mcpServers"] == (servers if servers is not None else ORIGINAL["mcpServers"])


def test_skill_toggle_keeps_placeholders_and_unknown_keys(config_file):
    set_skill_enabled("deep-research", False)
    _assert_untouched_except(config_file, skills={"deep-research": {"enabled": False}})


def test_mcp_toggle_keeps_placeholders_and_unknown_keys(config_file):
    set_mcp_server_enabled("github", False)
    servers = json.loads(json.dumps(ORIGINAL["mcpServers"]))
    servers["github"]["enabled"] = False
    _assert_untouched_except(config_file, servers=servers)
    with pytest.raises(LookupError):
        set_mcp_server_enabled("nope", True)


def test_capability_ops_use_the_raw_writer(config_file, monkeypatch):
    from types import SimpleNamespace

    from deerflow.capabilities import CapabilityRegistry, OpContext
    from deerflow.capabilities.modules import mcp as mcp_module
    from deerflow.capabilities.modules import skills as skills_module

    monkeypatch.setattr(skills_module, "_storage", lambda: SimpleNamespace(load_skills=lambda *, enabled_only: [SimpleNamespace(name="deep-research")]))
    reg = CapabilityRegistry(flags=lambda: {})
    reg.register(skills_module.MODULE)
    reg.register(mcp_module.MODULE)
    admin = OpContext(user_id="root", is_admin=True)
    asyncio.run(reg.invoke("skills.toggle", admin, {"name": "deep-research", "enabled": False}))
    asyncio.run(reg.invoke("mcp.toggle", admin, {"name": "github", "enabled": False}))
    servers = json.loads(json.dumps(ORIGINAL["mcpServers"]))
    servers["github"]["enabled"] = False
    _assert_untouched_except(config_file, skills={"deep-research": {"enabled": False}}, servers=servers)


def test_writer_creates_a_missing_file_with_both_sections(tmp_path):
    path = update_extensions_config_file(lambda raw: raw["skills"].update({"x": {"enabled": True}}), tmp_path / "new.json")
    assert json.loads(path.read_text(encoding="utf-8")) == {"mcpServers": {}, "skills": {"x": {"enabled": True}}}
