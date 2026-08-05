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
        return sandbox_router._APPVIEW_WS_SHIM % {"ws_prefix": json.dumps(f"/api/sandbox/appview-ws/{thread_id}")}

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
        assert sandbox_router._ws_same_origin(self._ws("https://nova.example", "nova.example")) is True

    def test_foreign_origin_rejected(self) -> None:
        assert sandbox_router._ws_same_origin(self._ws("https://evil.example", "nova.example")) is False

    def test_absent_origin_allowed(self) -> None:
        """Non-browser clients omit Origin; the thread-ownership check still applies."""
        assert sandbox_router._ws_same_origin(self._ws(None, "nova.example")) is True
