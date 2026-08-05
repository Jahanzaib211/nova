"""web_fetch must work without a Jina API key.

r.jina.ai used to serve anonymous requests. It now answers 401 without a key,
and `JinaClient.crawl` returns that as an ``Error:`` string which `web_fetch`
passed straight back to the model — so the tool was simply dead on every
deployment that had never set ``JINA_API_KEY``. Nova's own capability audit
caught it in the field ("web_fetch ❌ Jina API returned 401 — Invalid API key").

The fix falls back to a direct fetch + the readability extractor the tool
already used, so the key becomes an optional upgrade rather than a hard
dependency.
"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest

from deerflow.community.jina_ai import tools as web_fetch_mod

_PAGE = """<html><head><title>Hello</title></head>
<body><article><h1>Hello</h1><p>Real content from the origin server.</p></article></body></html>"""


@pytest.fixture(autouse=True)
def _no_tool_config(monkeypatch: pytest.MonkeyPatch):
    """Keep the tool off the real config.yaml."""
    monkeypatch.setattr(web_fetch_mod, "get_app_config", lambda: SimpleNamespace(get_tool_config=lambda name: None))


def _run(url: str = "https://example.test/page") -> str:
    return asyncio.run(web_fetch_mod.web_fetch_tool.ainvoke({"url": url}))


class TestFallback:
    def test_jina_401_falls_back_to_a_direct_fetch(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """The exact live failure."""

        async def dead_jina(self, url, **kwargs):
            return "Error: Jina API returned status 401: Invalid API key"

        async def ok_direct(url, timeout, proxy, trust_env):
            return _PAGE

        monkeypatch.setattr(web_fetch_mod.JinaClient, "crawl", dead_jina)
        monkeypatch.setattr(web_fetch_mod, "_direct_fetch", ok_direct)

        out = _run()
        assert not out.startswith("Error:"), out
        assert "Real content from the origin server." in out

    def test_jina_success_skips_the_fallback(self, monkeypatch: pytest.MonkeyPatch) -> None:
        async def ok_jina(self, url, **kwargs):
            return _PAGE

        called: list[str] = []

        async def spy_direct(url, timeout, proxy, trust_env):
            called.append(url)
            return _PAGE

        monkeypatch.setattr(web_fetch_mod.JinaClient, "crawl", ok_jina)
        monkeypatch.setattr(web_fetch_mod, "_direct_fetch", spy_direct)

        out = _run()
        assert "Real content from the origin server." in out
        assert called == [], "direct fetch must not run when Jina succeeded"

    def test_both_failing_reports_both_and_names_the_env_var(self, monkeypatch: pytest.MonkeyPatch) -> None:
        async def dead_jina(self, url, **kwargs):
            return "Error: Jina API returned status 401: Invalid API key"

        async def dead_direct(url, timeout, proxy, trust_env):
            return "Error: direct fetch returned status 403"

        monkeypatch.setattr(web_fetch_mod.JinaClient, "crawl", dead_jina)
        monkeypatch.setattr(web_fetch_mod, "_direct_fetch", dead_direct)

        out = _run()
        assert out.startswith("Error:")
        assert "401" in out and "403" in out, "both failure reasons must survive for the model to reason about"
        assert "JINA_API_KEY" in out, "the actionable remedy must be named"


class TestDirectFetch:
    def test_rejects_non_http_schemes(self) -> None:
        """No file:// or gopher:// exfiltration through the fallback."""
        out = asyncio.run(web_fetch_mod._direct_fetch("file:///etc/passwd", timeout=5, proxy=None, trust_env=False))
        assert out.startswith("Error: unsupported URL scheme")

    def test_non_200_is_an_error_string_not_an_exception(self, monkeypatch: pytest.MonkeyPatch) -> None:
        class _Resp:
            status_code = 404
            text = "nope"

        class _Client:
            def __init__(self, **kwargs):
                pass

            async def __aenter__(self):
                return self

            async def __aexit__(self, *a):
                return False

            async def get(self, url, headers=None, timeout=None):
                return _Resp()

        monkeypatch.setattr(web_fetch_mod.httpx, "AsyncClient", _Client)
        out = asyncio.run(web_fetch_mod._direct_fetch("https://example.test", timeout=5, proxy=None, trust_env=False))
        assert out == "Error: direct fetch returned status 404"

    def test_transport_errors_are_caught(self, monkeypatch: pytest.MonkeyPatch) -> None:
        class _Client:
            def __init__(self, **kwargs):
                pass

            async def __aenter__(self):
                return self

            async def __aexit__(self, *a):
                return False

            async def get(self, url, headers=None, timeout=None):
                raise ConnectionError("dns failure")

        monkeypatch.setattr(web_fetch_mod.httpx, "AsyncClient", _Client)
        out = asyncio.run(web_fetch_mod._direct_fetch("https://example.test", timeout=5, proxy=None, trust_env=False))
        assert out.startswith("Error: direct fetch failed: ConnectionError")

    def test_sends_a_browser_user_agent(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Plenty of origins 403 the default httpx UA, which would look like a broken fallback."""
        seen: dict = {}

        class _Resp:
            status_code = 200
            text = _PAGE

        class _Client:
            def __init__(self, **kwargs):
                seen["kwargs"] = kwargs

            async def __aenter__(self):
                return self

            async def __aexit__(self, *a):
                return False

            async def get(self, url, headers=None, timeout=None):
                seen["headers"] = headers
                return _Resp()

        monkeypatch.setattr(web_fetch_mod.httpx, "AsyncClient", _Client)
        asyncio.run(web_fetch_mod._direct_fetch("https://example.test", timeout=5, proxy=None, trust_env=True))
        assert "Mozilla/5.0" in seen["headers"]["User-Agent"]
        assert seen["kwargs"]["follow_redirects"] is True
        assert seen["kwargs"]["trust_env"] is True
