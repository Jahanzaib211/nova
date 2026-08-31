"""WebSockets across the whole served stack — the absproxy gap.

The gateway already bridges WebSockets for three of its four proxied HTML
surfaces: ttyd/noVNC (``/appview-ws``, reached via an injected
WebSocket-constructor shim) and the dev-server HMR bridges
(``/preview-ws`` / ``/lpreview-ws``). The fourth surface — ``absproxy``, the
Browser tab's fallback for a dev server started outside ``start_dev_server``
— had no WebSocket route at all, so a page served through it loaded fine and
its hot-reload socket silently died (Next's ``webpack-hmr`` / Vite's
``@vite/client`` reconnect-loop forever while content went stale).

These tests pin:

1. the ``/absproxy-ws`` route exists and shares the appview bridge's admission
   checks (same-origin, session-cookie auth, thread ownership);
2. the shim generalizes to prefix-swap semantics (a client socket resolved
   against the proxy's own ``<base>`` prefix must be re-pointed at the ``-ws``
   mount, not double-prefixed) while staying byte-compatible for appview;
3. every HTML-rewriting proxy injects exactly one shim, idempotently.
"""

from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace

import pytest

from app.gateway.routers import sandbox as sandbox_router

# ── Route registration ──────────────────────────────────────────────────────


class TestAbsproxyWsRoute:
    def test_route_is_registered(self) -> None:
        paths = {getattr(r, "path", "") for r in sandbox_router.router.routes}
        assert "/api/sandbox/absproxy-ws/{thread_id}/{port}/{path:path}" in paths, "the absproxy fallback has no WebSocket bridge"


# ── Admission checks (mirrors appview-ws; drift here would be a security bug) ──


def _fake_ws(origin: str = "https://nova.example", host: str = "nova.example", query: str = ""):
    headers = {"origin": origin, "host": host}
    url = SimpleNamespace(query=query)
    return SimpleNamespace(headers=headers, url=url, query=query)


@pytest.fixture
def owned_thread(monkeypatch):
    async def _owning(ws, tid):
        return True

    monkeypatch.setattr("app.gateway.ws_guards.ws_caller_owns_thread", _owning)


@pytest.fixture
def fake_provider(monkeypatch):
    import deerflow.sandbox as sandbox_pkg

    provider = _FakeProvider()
    monkeypatch.setattr(sandbox_pkg, "get_sandbox_provider", lambda: provider)
    return provider


class _FakeProvider:
    def __init__(self, base_url: str | None = "http://localhost:39123"):
        self._base_url = base_url

    def get_preview_endpoint(self, *a, **k):  # pragma: no cover - presence is the contract
        return None

    def acquire(self, thread_id: str) -> str:
        return f"aio:{thread_id}"

    def get(self, sandbox_id: str):
        return SimpleNamespace(base_url=self._base_url, _client=None)


@pytest.fixture
def wired(monkeypatch):
    """Own-thread + fake provider + captured bridge."""
    import deerflow.sandbox as sandbox_pkg

    provider = _FakeProvider()
    monkeypatch.setattr(sandbox_pkg, "get_sandbox_provider", lambda: provider)

    async def _owning(ws, tid):
        return True

    monkeypatch.setattr("app.gateway.ws_guards.ws_caller_owns_thread", _owning)

    bridged: dict[str, object] = {}
    accepted: list[bool] = []
    closed: list[int] = []

    async def fake_user(ws):
        return SimpleNamespace(id="u1")

    async def fake_bridge(websocket, upstream_url):
        bridged["url"] = upstream_url

    async def accept():
        accepted.append(True)

    async def close(code=1000):
        closed.append(code)

    ws = _fake_ws()
    ws.accept = accept
    ws.close = close
    ws.accept_calls = accepted
    ws.close_codes = closed

    monkeypatch.setattr("app.gateway.ws_guards.ws_user", fake_user)

    monkeypatch.setattr(sandbox_router, "_mark_sandbox_active", lambda thread_id: None)
    monkeypatch.setattr(sandbox_router, "_bridge_ws", fake_bridge)
    return SimpleNamespace(ws=ws, bridged=bridged)


@pytest.mark.asyncio
async def test_absproxy_ws_bridges_to_the_container_relay(wired) -> None:
    """The upstream hop must be ``/proxy/`` — the one that strips the prefix.

    This asserted ``/absproxy/4321/...`` until 2026-08-31. The sandbox exposes two
    relays and only one is right for a dev server not configured with a matching
    basePath. Measured on a live sandbox image, with a server logging the paths it
    was actually asked for::

        /proxy/8788/_next/webpack-hmr     -> server saw /_next/webpack-hmr
        /absproxy/8788/_next/webpack-hmr  -> server saw /absproxy/8788/_next/...

    ``/proxy/`` does forward the upgrade, so the old hop was not "the WebSocket
    route" — it was a route that upgraded against a path no dev server serves.
    That is why HMR through the Browser tab's fallback never reconnected and the
    preview silently went stale until a manual reload.

    The public route name stays ``absproxy-ws``; only the upstream hop moved, so
    it now matches the HTTP side (``_absproxy_impl``).
    """
    await sandbox_router.proxy_absproxy_ws(wired.ws, "thread-abc", 4321, "_next/webpack-hmr")
    assert wired.bridged["url"] == "ws://localhost:39123/proxy/4321/_next/webpack-hmr"
    assert wired.ws.accept_calls == [True]


@pytest.mark.asyncio
async def test_absproxy_ws_forwards_query_string(wired) -> None:
    ws = wired.ws
    ws.url = SimpleNamespace(query="token=x")
    await sandbox_router.proxy_absproxy_ws(ws, "thread-abc", 4321, "hmr")
    assert wired.bridged["url"] == "ws://localhost:39123/proxy/4321/hmr?token=x"


def test_both_hops_use_the_same_relay() -> None:
    """HTTP and WebSocket must not disagree about which relay strips the prefix.

    They did disagree, briefly, mid-fix: the HTTP hop was switched to ``/proxy/``
    while the socket still used ``/absproxy/``, so a page rendered correctly and
    then could not hot-reload. Cheap to assert, and the failure it prevents is
    invisible in the UI.
    """
    from pathlib import Path

    src = Path(sandbox_router.__file__).read_text(encoding="utf-8")
    assert "/proxy/{port}/{path}" in src
    assert "/absproxy/{port}/{path}" not in src, "one of the two hops is still on the path-preserving relay"


@pytest.mark.asyncio
async def test_absproxy_ws_rejects_cross_origin(monkeypatch) -> None:
    from app.gateway.ws_guards import reject as _unused  # noqa: F401 — guards exist

    ws = _fake_ws(origin="https://evil.example")
    calls: list[int] = []

    async def close(code=1000):
        calls.append(code)

    ws.close = close
    await sandbox_router.proxy_absproxy_ws(ws, "t", 4321, "hmr")
    assert calls == [1008]


@pytest.mark.asyncio
async def test_absproxy_ws_rejects_unauthenticated(monkeypatch) -> None:
    async def no_user(ws):
        return None

    monkeypatch.setattr("app.gateway.ws_guards.ws_user", no_user)
    ws = _fake_ws()
    calls: list[int] = []

    async def close(code=1000):
        calls.append(code)

    ws.close = close
    await sandbox_router.proxy_absproxy_ws(ws, "t", 4321, "hmr")
    assert calls == [1008]


@pytest.mark.asyncio
async def test_absproxy_ws_rejects_non_owner(monkeypatch) -> None:
    async def _denying(ws, tid):
        return False

    monkeypatch.setattr("app.gateway.ws_guards.ws_caller_owns_thread", _denying)

    async def fake_user(ws):
        return SimpleNamespace(id="u1")

    monkeypatch.setattr("app.gateway.ws_guards.ws_user", fake_user)
    ws = _fake_ws()
    calls: list[int] = []

    async def close(code=1000):
        calls.append(code)

    ws.close = close
    await sandbox_router.proxy_absproxy_ws(ws, "someone-elses", 4321, "hmr")
    assert calls == [1008]


# ── The generalized shim ────────────────────────────────────────────────────


class TestGeneralizedShim:
    def test_swap_semantics_replace_the_plain_prefix(self) -> None:
        """A socket URL resolved against <base href="/api/sandbox/preview/t1/">
        arrives with that plain prefix in its pathname. The shim must swap it
        for the -ws mount, not prepend to it."""
        shim = sandbox_router._ws_shim("/api/sandbox/preview-ws/t1", "/api/sandbox/preview/t1")
        stripped = shim.replace(" ", "")
        assert "indexOf(B)===0" in stripped, "no base-prefix swap branch"
        assert "slice(B.length)" in stripped

    def test_appview_default_keeps_prepend_semantics(self) -> None:
        """With no base prefix the shim behaves exactly as before: prepend P."""
        shim = sandbox_router._ws_shim("/api/sandbox/appview-ws/t1")
        stripped = shim.replace(" ", "")
        assert 'B=""' in stripped
        assert "slice(B.length)" not in stripped or 'B=""' in stripped
        # The prepend branch is still present.
        assert "x.pathname=P+x.pathname" in stripped

    def test_preserves_websocket_constants_and_cross_origin_rule(self) -> None:
        shim = sandbox_router._ws_shim("/p", "/b")
        for const in ("CONNECTING", "OPEN", "CLOSING", "CLOSED"):
            assert const in shim
        assert "x.host!==window.location.host" in shim.replace(" ", "")


class TestShimInjection:
    HTML = "<html><head><title>x</title></head><body></body></html>"

    def test_injects_exactly_one_shim(self) -> None:
        out = sandbox_router._inject_ws_shim(self.HTML, "/ws/t1", "/http/t1")
        assert out.count("window.WebSocket") == 2  # Orig + override
        assert '<base href="/http/t1/">' in out

    def test_is_idempotent(self) -> None:
        once = sandbox_router._inject_ws_shim(self.HTML, "/ws/t1", "/http/t1")
        twice = sandbox_router._inject_ws_shim(once, "/ws/t1", "/http/t1")
        assert twice == once

    def test_no_head_is_a_noop(self) -> None:
        assert sandbox_router._inject_ws_shim("<html/>", "/ws", "/b") == "<html/>"

    def test_existing_base_tag_not_duplicated(self) -> None:
        html = '<html><head><base href="/somewhere/">t</head></html>'
        out = sandbox_router._inject_ws_shim(html, "/ws/t1", "/http/t1")
        assert out.count("<base ") == 1


# ── Every HTML-rewriting proxy injects the shim ─────────────────────────────


def _ready_handle(label: str = "app") -> SimpleNamespace:
    return SimpleNamespace(status="ready", host="127.0.0.1", port=39999, label=label)


class TestProxyHtmlInjectsShim:
    @pytest.fixture
    def preview_html(self, owned_thread, monkeypatch) -> str:
        """Run _proxy_dev_server against a fake ready handle + fake upstream."""
        import httpx

        monkeypatch.setattr(sandbox_router, "get_dev_server", lambda tid, label: _ready_handle(label))
        monkeypatch.setattr(sandbox_router, "discover_live_preview", _async_none)

        async def fake_fetch(*a, **k):
            return httpx.Response(
                200,
                content=b"<html><head></head><body>app</body></html>",
                headers={"content-type": "text/html"},
            )

        monkeypatch.setattr(httpx, "AsyncClient", lambda **kw: _FakeAsyncClient(fake_fetch))
        resp = asyncio.run(sandbox_router._proxy_dev_server("t1", "app", "", _request()))
        return bytes(resp.body).decode()

    def test_preview_html_carries_preview_ws_prefix(self, preview_html) -> None:
        assert "/api/sandbox/preview-ws/t1" in preview_html
        assert "<base" in preview_html

    def test_labeled_preview_html_carries_lpreview_ws_prefix(self, owned_thread, monkeypatch) -> None:
        import httpx

        monkeypatch.setattr(sandbox_router, "get_dev_server", lambda tid, label: _ready_handle("web"))
        monkeypatch.setattr(sandbox_router, "discover_live_preview", _async_none)

        async def fake_fetch(*a, **k):
            return httpx.Response(200, content=b"<html><head></head><body>app</body></html>", headers={"content-type": "text/html"})

        monkeypatch.setattr(httpx, "AsyncClient", lambda **kw: _FakeAsyncClient(fake_fetch))
        resp = asyncio.run(sandbox_router._proxy_dev_server("t1", "web", "", _request()))
        body = bytes(resp.body).decode()
        assert "/api/sandbox/lpreview-ws/t1/web" in body

    def test_absproxy_html_carries_absproxy_ws_prefix(self, owned_thread, fake_provider, monkeypatch) -> None:
        import httpx

        async def fake_fetch(*a, **k):
            return httpx.Response(
                200,
                content=b"<html><head></head><body>app</body></html>",
                headers={"content-type": "text/html"},
            )

        monkeypatch.setattr(httpx, "AsyncClient", lambda **kw: _FakeAsyncClient(fake_fetch))
        request = _request()
        resp = asyncio.run(sandbox_router._absproxy_impl("t1", 4321, "", request))
        body = bytes(resp.body).decode()
        assert "/api/sandbox/absproxy-ws/t1/4321" in body
        assert "<base" in body

    def test_no_head_survives_absproxy(self, owned_thread, fake_provider, monkeypatch) -> None:
        import httpx

        async def fake_fetch(*a, **k):
            return httpx.Response(200, content=b"<html><body>no head</body></html>", headers={"content-type": "text/html"})

        monkeypatch.setattr(httpx, "AsyncClient", lambda **kw: _FakeAsyncClient(fake_fetch))
        resp = asyncio.run(sandbox_router._absproxy_impl("t1", 4321, "", _request()))
        body = bytes(resp.body).decode()
        assert "window.WebSocket" not in body  # nothing to anchor on; page loads as before


async def _async_none(*a, **k):
    return None


class _FakeAsyncClient:
    def __init__(self, fetch, *args, **kwargs):
        self._fetch = fetch

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    async def request(self, method, url, headers=None, content=None):
        return await self._fetch(method, url, headers, content)


def _request() -> object:
    from fastapi import Request

    async def receive():
        return {"type": "http.request", "body": b"", "more_body": False}

    return Request(
        {
            "type": "http",
            "method": "GET",
            "path": "/",
            "headers": [],
            "query_string": b"",
            "client": ("x", 1),
            "server": ("y", 2),
            "scheme": "http",
        },
        receive=receive,
    )
