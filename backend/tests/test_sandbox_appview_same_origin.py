"""The sandbox's live terminal / VNC panes must be same-origin.

`terminal_url` used to hand the browser absolute
``http://localhost:{published_port}`` URLs, on the documented assumption that
"the browser shares the Docker host". That is true only for local development.
Production (nova.alilabsx.com) is Docker Compose behind a Cloudflare tunnel and
staging is k3s — in both, the visitor's browser cannot reach the container's
published port at all, and the app shell's own CSP (`frame-src 'self' blob:`)
would refuse to frame a foreign origin regardless. Both panes were therefore
permanently blank once deployed, and worked perfectly on the developer's laptop.

The fix routes them through `/api/sandbox/appview/{thread_id}/`, so these tests
pin (a) no absolute host URL ever escapes the endpoint again and (b) the proxy
that makes that possible behaves.
"""

from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace

import pytest

from app.gateway.routers import sandbox as sandbox_router


class _FakeProvider:
    """Stands in for AioSandboxProvider — `get_preview_endpoint` is the duck-type gate."""

    def __init__(self, base_url: str | None = "http://localhost:39123"):
        self._base_url = base_url

    def get_preview_endpoint(self, *a, **k):  # pragma: no cover - presence is the contract
        return None

    def acquire(self, thread_id: str) -> str:
        return f"aio:{thread_id}"

    def get(self, sandbox_id: str):
        return SimpleNamespace(base_url=self._base_url, _client=None)


@pytest.fixture
def owned_thread(monkeypatch):
    monkeypatch.setattr(sandbox_router, "_caller_owns_thread", lambda thread_id: True)


@pytest.fixture
def fake_provider(monkeypatch):
    provider = _FakeProvider()
    import deerflow.sandbox as sandbox_pkg

    monkeypatch.setattr(sandbox_pkg, "get_sandbox_provider", lambda: provider)
    return provider


class TestTerminalUrl:
    def test_returns_same_origin_relative_paths(self, owned_thread, fake_provider) -> None:
        out = asyncio.run(sandbox_router.terminal_url("thread-abc"))
        assert out["terminal"].startswith("/api/sandbox/appview/thread-abc/")
        assert out["vnc"] == "/api/sandbox/appview/thread-abc/vnc/index.html"

    def test_never_returns_an_absolute_localhost_url(self, owned_thread, fake_provider) -> None:
        """The exact regression: absolute URLs are unreachable off the Docker host."""
        out = asyncio.run(sandbox_router.terminal_url("thread-abc"))
        for key in ("terminal", "vnc"):
            value = out[key] or ""
            assert not value.startswith("http://"), f"{key} leaked an absolute URL: {value}"
            assert not value.startswith("https://"), f"{key} leaked an absolute URL: {value}"
            assert "localhost" not in value, f"{key} still points at localhost: {value}"

    def test_reports_the_published_port_for_diagnostics(self, owned_thread, fake_provider) -> None:
        out = asyncio.run(sandbox_router.terminal_url("thread-abc"))
        assert out["port"] == 39123

    def test_unowned_thread_is_404(self, monkeypatch, fake_provider) -> None:
        from fastapi import HTTPException

        monkeypatch.setattr(sandbox_router, "_caller_owns_thread", lambda thread_id: False)
        with pytest.raises(HTTPException) as exc:
            asyncio.run(sandbox_router.terminal_url("someone-elses-thread"))
        assert exc.value.status_code == 404

    def test_missing_base_url_degrades_without_raising(self, owned_thread, monkeypatch) -> None:
        provider = _FakeProvider(base_url=None)
        import deerflow.sandbox as sandbox_pkg

        monkeypatch.setattr(sandbox_pkg, "get_sandbox_provider", lambda: provider)
        out = asyncio.run(sandbox_router.terminal_url("thread-abc"))
        assert out == {"terminal": None, "vnc": None, "reason": "sandbox unavailable"}


class TestAppviewPrefix:
    def test_prefix_shape(self) -> None:
        assert sandbox_router._appview_prefix("t1") == "/api/sandbox/appview/t1"

    def test_routes_are_registered_for_every_verb(self) -> None:
        paths = {r.path for r in sandbox_router.router.routes if "appview" in getattr(r, "path", "")}
        assert "/api/sandbox/appview/{thread_id}/{path:path}" in paths
        assert "/api/sandbox/appview-ws/{thread_id}/{path:path}" in paths
        methods: set[str] = set()
        for r in sandbox_router.router.routes:
            if getattr(r, "path", "") == "/api/sandbox/appview/{thread_id}/{path:path}":
                methods |= set(getattr(r, "methods", set()) or set())
        assert {"GET", "POST", "PUT", "DELETE", "PATCH", "OPTIONS", "HEAD"} <= methods


class TestWebSocketShim:
    """ttyd and noVNC build their socket URL in JS from `window.location`.

    A `<base href>` tag cannot reach that, so the HTML proxy injects a
    WebSocket-constructor shim. If the shim is dropped, both panes render but
    never connect — the failure mode looks like a hung terminal, not an error.
    """

    def _shim(self, thread_id: str = "t1") -> str:
        return sandbox_router._ws_shim(f"/api/sandbox/appview-ws/{thread_id}")

    def test_shim_targets_the_ws_route(self) -> None:
        assert '"/api/sandbox/appview-ws/t1"' in self._shim()

    def test_shim_preserves_websocket_constants(self) -> None:
        """Libraries branch on WebSocket.OPEN; a naive wrapper drops them."""
        shim = self._shim()
        for const in ("CONNECTING", "OPEN", "CLOSING", "CLOSED"):
            assert const in shim

    def test_shim_leaves_cross_origin_sockets_alone(self) -> None:
        assert "x.host!==window.location.host" in self._shim().replace(" ", "")


class TestWsSameOriginGuard:
    """Anti cross-site-WebSocket-hijacking, shared by the dev-server and appview bridges."""

    def _ws(self, origin: str | None, host: str | None):
        headers = {}
        if origin is not None:
            headers["origin"] = origin
        if host is not None:
            headers["host"] = host
        return SimpleNamespace(headers=headers)

    def test_matching_origin_allowed(self) -> None:
        from app.gateway.ws_guards import ws_same_origin

        assert ws_same_origin(self._ws("https://nova.example", "nova.example")) is True

    def test_foreign_origin_rejected(self) -> None:
        from app.gateway.ws_guards import ws_same_origin

        assert ws_same_origin(self._ws("https://evil.example", "nova.example")) is False

    def test_absent_origin_allowed(self) -> None:
        """Non-browser clients omit Origin; the thread-ownership check still applies."""
        from app.gateway.ws_guards import ws_same_origin

        assert ws_same_origin(self._ws(None, "nova.example")) is True


class TestFramedCsp:
    """Framed panes must carry the session cookie to authenticate against the gateway.

    The iframed ttyd/noVNC/preview page loads its own assets (JS, websocket
    handshake) from the gateway. With an opaque sandbox origin those
    sub-resource requests carry no cookies, so the global auth middleware 401s
    every asset and the ws same-origin guard rejects ``Origin: null`` — every
    pane goes blank. ``allow-same-origin`` keeps the sandbox boundary while
    letting the framed content authenticate like any same-origin page.
    """

    def test_framed_csp_grants_same_origin(self) -> None:
        csp = sandbox_router._FRAMED_SANDBOX_CSP
        assert csp.startswith("sandbox allow-scripts")
        assert "allow-same-origin" in csp

    def test_opaque_csp_keeps_absproxy_sandboxed(self) -> None:
        csp = sandbox_router._OPAQUE_SANDBOX_CSP
        assert csp.startswith("sandbox allow-scripts")
        assert "allow-same-origin" not in csp

    def test_framed_csp_applied_to_appview_responses(self, owned_thread, fake_provider, monkeypatch) -> None:
        import httpx

        async def fake_fetch(*a, **k):
            return httpx.Response(200, content=b"<html><head></head><body>ui</body></html>", headers={"content-type": "text/html"})

        monkeypatch.setattr(httpx, "AsyncClient", lambda **kw: _FakeAsyncClient(fake_fetch))

        from fastapi import Request

        async def receive():
            return {"type": "http.request", "body": b"", "more_body": False}

        request = Request(
            {"type": "http", "method": "GET", "path": "/", "headers": [], "query_string": b"", "client": ("x", 1), "server": ("y", 2), "scheme": "http"},
            receive=receive,
        )
        resp = __import__("asyncio").run(sandbox_router._proxy_appview("thread-abc", "terminal", request))
        csp = resp.headers.get("content-security-policy")
        # The framed CSP, plus the frame-ancestors restriction added alongside
        # `Sec-Fetch-Dest` selection — `allow-same-origin` is only safe while
        # something limits who can frame the response in the first place.
        assert csp.startswith(sandbox_router._FRAMED_SANDBOX_CSP)
        assert "allow-same-origin" in csp
        assert "frame-ancestors 'self'" in csp


class _FakeAsyncClient:
    """Minimal httpx.AsyncClient replacement for proxy tests."""

    def __init__(self, fetch, *args, **kwargs):
        self._fetch = fetch

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    async def request(self, method, url, headers=None, content=None):
        return await self._fetch(method, url, headers, content)


class TestPrefixHtmlUrls:
    def test_rewrites_root_absolute_assets_under_prefix(self) -> None:
        html = '<html><head><link rel="stylesheet" href="/_next/static/app.css"></head><body><a href="/about">x</a><form action="/go"></form><img src="/img.png"></body></html>'
        out = sandbox_router._prefix_html_urls(html, "/api/sandbox/appview/t1")
        assert 'href="/api/sandbox/appview/t1/_next/static/app.css"' in out
        assert 'href="/api/sandbox/appview/t1/about"' in out
        assert 'action="/api/sandbox/appview/t1/go"' in out
        assert 'src="/api/sandbox/appview/t1/img.png"' in out

    def test_is_idempotent_on_a_second_pass(self) -> None:
        """A page proxied twice (or already made prefix-relative by the browser)
        must never be double-prefixed."""
        html = '<html><head><script src="/_next/static/chunks/x.js"></script></head><body><a href="/foo">f</a></body></html>'
        once = sandbox_router._prefix_html_urls(html, "/api/sandbox/appview/t1")
        twice = sandbox_router._prefix_html_urls(once, "/api/sandbox/appview/t1")
        assert twice == once
        assert "/api/sandbox/appview/t1/api/sandbox/appview/t1" not in twice

    def test_leaves_absolute_http_urls_alone(self) -> None:
        html = '<a href="https://cdn.example/x.css">x</a><script src="//other.example/stat.js"></script>'
        out = sandbox_router._prefix_html_urls(html, "/p")
        assert "https://cdn.example/x.css" in out
        assert "//other.example/stat.js" in out

    def test_empty_and_prefixless_input_are_noops(self) -> None:
        assert sandbox_router._prefix_html_urls("", "/p") == ""
        assert sandbox_router._prefix_html_urls("<html/>", "") == "<html/>"


# ── The upstream-socket leak (ttyd PTY held after the browser closed) ─────────

import asyncio as _aio  # noqa: E402
import contextlib as _ctx  # noqa: E402
from unittest.mock import AsyncMock, MagicMock  # noqa: E402


class _SilentUpstream:
    """An upstream that never sends and never closes -- exactly ttyd at idle.

    Under the old ``asyncio.gather(...)`` this is what pinned the bridge open:
    the client pump returned on disconnect, but this side stayed parked in
    ``async for`` forever, holding the socket and its PTY until the container
    died.
    """

    def __init__(self):
        self.closed = False
        self.send = AsyncMock()

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        self.closed = True
        return False

    def __aiter__(self):
        return self

    async def __anext__(self):
        await _aio.Event().wait()  # never resolves
        raise AssertionError("unreachable")


@pytest.mark.asyncio
async def test_client_disconnect_releases_the_upstream(monkeypatch):
    """The bridge must finish promptly when the browser goes away."""
    from app.gateway.routers import sandbox as mod

    upstream = _SilentUpstream()
    monkeypatch.setattr(mod, "_ws", MagicMock(connect=MagicMock(return_value=upstream)), raising=False)

    ws = MagicMock()
    ws.receive = AsyncMock(return_value={"type": "websocket.disconnect"})
    ws.close = AsyncMock()

    import sys
    import types

    fake = types.ModuleType("websockets")
    fake.connect = MagicMock(return_value=upstream)
    monkeypatch.setitem(sys.modules, "websockets", fake)

    # Under gather() this never returns; the timeout is the assertion.
    with _ctx.suppress(TimeoutError):
        await _aio.wait_for(mod._bridge_ws(ws, "ws://sandbox:7681/ws"), timeout=5.0)

    assert upstream.closed, "the upstream connection was never exited -- the PTY leaked"
    ws.close.assert_awaited()
