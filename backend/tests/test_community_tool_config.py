"""``nova.community._tool_config.tool_config_extras`` replaces four
identical ``_get_tool_config`` copies (SearXNG, Browserless, Crawl4AI ×2)."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from nova.community import _tool_config
from nova.community._tool_config import tool_config_extras


def _config(entries):
    return SimpleNamespace(get_tool_config=entries.get)


def test_extras_none_and_empty(monkeypatch):
    entries = {"web_fetch": SimpleNamespace(model_extra={"base_url": "http://b:3000"}), "bare": SimpleNamespace(model_extra=None)}
    monkeypatch.setattr(_tool_config, "get_app_config", lambda: _config(entries))
    assert tool_config_extras("web_fetch") == {"base_url": "http://b:3000"}
    assert tool_config_extras("bare") == {}
    assert tool_config_extras("missing") is None


@pytest.mark.parametrize(
    "module",
    ["nova.community.searxng.tools", "nova.community.browserless.tools", "nova.community.crawl4ai.tools", "nova.community.crawl4ai.crawl_tool"],
)
def test_every_web_tool_module_uses_the_shared_helper(module):
    import importlib

    assert importlib.import_module(module)._get_tool_config is tool_config_extras
