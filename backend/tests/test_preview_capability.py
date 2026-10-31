"""Signed preview capabilities (2026-10-31).

The Browser tab frames agent-built apps in an opaque-origin sandbox, so Chromium
sends their asset requests without the session cookie and the auth middleware
401'd every stylesheet, script and font -- the unstyled Browser tab, measured in
the live gateway log as ``GET …/preview/{tid}/ 200`` followed by ``401`` for
every sub-resource. Firefox still sent the cookie, which is why it rendered
there. The credential now travels in the path.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest
from fastapi import FastAPI, Request
from starlette.testclient import TestClient

from app.gateway import preview_capability as pc
from app.gateway.auth_middleware import AuthMiddleware
from app.gateway.csrf_middleware import is_csrf_exempt_path

THREAD = "c31909b1-deb8-4e0b-a98f-200212d4dd4b"
USER = SimpleNamespace(id="4da426d3-eba9-4318-95df-372a887d4b97", token_version=4, is_forbidden=False, email="u@x", system_role="user", needs_setup=False)
NOW = 1_793_400_000


@pytest.fixture(autouse=True)
def _secret_and_users(monkeypatch):
    monkeypatch.setattr("app.gateway.auth.config.get_auth_config", lambda: SimpleNamespace(jwt_secret="test-secret-for-capabilities-32bytes!"))
    users = {USER.id: USER}

    class _Provider:
        async def get_user(self, user_id):
            return users.get(user_id)

    monkeypatch.setattr("app.gateway.deps.get_local_provider", lambda: _Provider())
    monkeypatch.setenv("DEER_FLOW_AUTH_DISABLED", "")
    return users


def _resolve(path: str, now: int = NOW):
    import asyncio

    cap = pc.capability_in_path(path)
    assert cap is not None
    return asyncio.run(pc.resolve_capability(cap, now=now))


def _preview_path(segment: str, rest: str = "_next/static/css/app.css") -> str:
    return f"/api/sandbox/preview/{segment}/{rest}"


class TestMinting:
    def test_segment_carries_thread_user_expiry_and_signature(self):
        seg = pc.mint_segment(THREAD, "preview", "app", USER, now=NOW)
        thread_id, cap = pc.split_segment(seg)
        assert thread_id == THREAD
        user_id, expiry, signature = cap.split("~")
        assert user_id == USER.id and int(expiry) > NOW and len(signature) == 22

    def test_url_is_stable_within_a_window(self):
        # The Browser tab keys its iframe on the URL; a per-poll token would
        # reload the preview every few seconds.
        assert pc.mint_segment(THREAD, "preview", "app", USER, now=NOW) == pc.mint_segment(THREAD, "preview", "app", USER, now=NOW + 600)

    def test_no_capability_without_a_user(self):
        assert pc.mint_segment(THREAD, "preview", "app", None) == THREAD

    def test_thread_of_strips_the_capability(self):
        assert pc.thread_of(pc.mint_segment(THREAD, "preview", "app", USER)) == THREAD
        assert pc.thread_of(THREAD) == THREAD


class TestVerification:
    def test_a_minted_capability_authenticates_its_user(self):
        seg = pc.mint_segment(THREAD, "preview", "app", USER, now=NOW)
        assert _resolve(_preview_path(seg)) is USER

    def test_it_covers_the_hmr_socket_of_the_same_surface(self):
        seg = pc.mint_segment(THREAD, "preview", "app", USER, now=NOW)
        assert _resolve(f"/api/sandbox/preview-ws/{seg}/_next/webpack-hmr") is USER

    def test_labels_and_ports_are_bound(self):
        dash = pc.mint_segment(THREAD, "preview", "dashboard", USER, now=NOW)
        assert _resolve(f"/api/sandbox/lpreview/{dash}/dashboard/") is USER
        assert _resolve(f"/api/sandbox/lpreview/{dash}/api/") is None
        assert _resolve(_preview_path(dash)) is None
        port = pc.mint_segment(THREAD, "absproxy", "4100", USER, now=NOW)
        assert _resolve(f"/api/sandbox/absproxy/{port}/4100/x.js") is USER
        assert _resolve(f"/api/sandbox/absproxy/{port}/5432/") is None

    def test_another_thread_cannot_reuse_it(self):
        seg = pc.mint_segment(THREAD, "preview", "app", USER, now=NOW)
        forged = seg.replace(THREAD, "00000000-0000-0000-0000-000000000000")
        assert _resolve(_preview_path(forged)) is None

    def test_it_expires(self):
        seg = pc.mint_segment(THREAD, "preview", "app", USER, now=NOW)
        expiry = int(pc.split_segment(seg)[1].split("~")[1])
        assert _resolve(_preview_path(seg), now=expiry - 1) is USER
        assert _resolve(_preview_path(seg), now=expiry) is None

    def test_a_password_change_or_ban_revokes_it(self, _secret_and_users):
        seg = pc.mint_segment(THREAD, "preview", "app", USER, now=NOW)
        _secret_and_users[USER.id] = SimpleNamespace(**{**vars(USER), "token_version": 5})
        assert _resolve(_preview_path(seg)) is None
        _secret_and_users[USER.id] = SimpleNamespace(**{**vars(USER), "is_forbidden": True})
        assert _resolve(_preview_path(seg)) is None

    @pytest.mark.parametrize("cap", ["junk", "a~b", "u~notanumber~sig", "~~"])
    def test_malformed_capabilities_are_refused_not_ignored(self, cap):
        # Parsed (so the middleware refuses it) rather than treated as absent
        # (which would let the request fall back to the cookie).
        parsed = pc.capability_in_path(_preview_path(f"{THREAD}~{cap}"))
        assert parsed is not None
        import asyncio

        assert asyncio.run(pc.resolve_capability(parsed, now=NOW)) is None

    def test_plain_paths_carry_no_capability(self):
        assert pc.capability_in_path(_preview_path(THREAD)) is None
        assert pc.capability_in_path("/api/threads/x~y/state") is None
        assert pc.capability_in_path("/api/sandbox/appview/x~y/vnc/") is None


def _app():
    app = FastAPI()
    app.add_middleware(AuthMiddleware)

    @app.api_route("/api/sandbox/preview/{segment}/{path:path}", methods=["GET", "POST"])
    async def preview(segment: str, path: str, request: Request):
        return {"user": str(request.state.user.id), "source": request.state.auth_source}

    return app


class TestMiddleware:
    def test_cookieless_asset_with_a_capability_is_served(self):
        seg = pc.mint_segment(THREAD, "preview", "app", USER)
        r = TestClient(_app()).get(_preview_path(seg))
        assert r.status_code == 200
        assert r.json() == {"user": USER.id, "source": "preview_capability"}

    def test_cookieless_asset_without_one_is_refused(self):
        assert TestClient(_app()).get(_preview_path(THREAD)).status_code == 401

    def test_a_bad_capability_never_falls_back_to_the_cookie(self, monkeypatch):
        # Otherwise a forged segment would let a cross-site form ride the
        # session cookie past the CSRF exemption below.
        async def _cookie_user(_request):
            return USER

        monkeypatch.setattr("app.gateway.deps.get_current_user_from_request", _cookie_user)
        client = TestClient(_app(), cookies={"access_token": "valid-session"})
        assert client.get(_preview_path(f"{THREAD}~{USER.id}~9999999999~forgedforgedforgedforg")).status_code == 401

    def test_auth_disabled_deployments_are_not_locked_out_by_a_stale_link(self, monkeypatch):
        monkeypatch.setenv("DEER_FLOW_AUTH_DISABLED", "1")
        r = TestClient(_app()).get(_preview_path(f"{THREAD}~{USER.id}~1~stale"))
        assert r.status_code == 200


class TestCsrf:
    def test_capability_paths_are_exempt(self):
        seg = pc.mint_segment(THREAD, "preview", "app", USER)
        assert is_csrf_exempt_path(_preview_path(seg, "api/contact"))

    def test_cookie_authenticated_preview_paths_are_not(self):
        assert not is_csrf_exempt_path(_preview_path(THREAD, "api/contact"))


class TestProxySurfaces:
    """The capability must survive into every URL the proxy hands the browser."""

    def _serve(self, monkeypatch, seen: list[str]):
        import httpx

        from app.gateway.routers import sandbox as sb

        monkeypatch.setattr(sb, "_caller_owns_thread", lambda tid: tid == THREAD)
        monkeypatch.setattr(sb, "_mark_sandbox_active", lambda _tid: None)
        monkeypatch.setattr(sb, "get_dev_server", lambda _tid, _label="app": SimpleNamespace(status="ready", host="dev", port=4100, label="app"))
        page = b'<html><head><link rel="stylesheet" href="/_next/static/css/app.css"></head><body><a href="/about">About</a></body></html>'

        def handler(req):
            seen.append(req.url.path)
            return httpx.Response(200, headers={"content-type": "text/html", "link": "</_next/static/media/a.woff2>; rel=preload"}, content=page)

        real = httpx.AsyncClient
        monkeypatch.setattr(sb.httpx, "AsyncClient", lambda **kw: real(transport=httpx.MockTransport(handler), **kw))
        return sb

    def test_rewritten_assets_carry_the_capability_and_upstream_sees_a_plain_path(self, monkeypatch):
        import asyncio

        seen: list[str] = []
        sb = self._serve(monkeypatch, seen)
        seg = pc.mint_segment(THREAD, "preview", "app", USER)

        async def receive():
            return {"type": "http.request", "body": b"", "more_body": False}

        req = Request({"type": "http", "method": "GET", "path": f"/api/sandbox/preview/{seg}/", "headers": [], "query_string": b""}, receive)
        resp = asyncio.run(sb._proxy_dev_server(seg, "app", "", req))
        body = resp.body.decode()
        assert resp.status_code == 200
        assert f'href="/api/sandbox/preview/{seg}/_next/static/css/app.css"' in body
        assert f"/api/sandbox/preview-ws/{seg}" in body
        assert resp.headers["link"] == f"</api/sandbox/preview/{seg}/_next/static/media/a.woff2>; rel=preload"
        assert resp.headers["referrer-policy"] == "same-origin"
        assert seen == ["/"]

    def test_dev_status_hands_out_capability_urls(self, monkeypatch):
        from app.gateway.routers import sandbox as sb

        monkeypatch.setattr(sb, "get_current_user", lambda: USER)
        url = sb._preview_url(THREAD, "app")
        assert url.startswith(f"/api/sandbox/preview/{THREAD}~{USER.id}~") and url.endswith("/")
        assert _resolve(url + "x.js") is USER
        absproxy = sb._absproxy_url(THREAD, 4100)
        assert absproxy.startswith(f"/api/sandbox/absproxy/{THREAD}~") and absproxy.endswith("/4100/")
        assert _resolve(absproxy + "x.js") is USER

    def test_dev_status_skips_capabilities_when_auth_is_disabled(self, monkeypatch):
        from app.gateway.routers import sandbox as sb

        monkeypatch.setenv("DEER_FLOW_AUTH_DISABLED", "1")
        monkeypatch.setattr(sb, "get_current_user", lambda: USER)
        assert sb._preview_url(THREAD, "app") == f"/api/sandbox/preview/{THREAD}/"


class TestSocketAdmission:
    def _ws(self, path: str, origin: str | None):
        headers = {"host": "nova.example"}
        if origin is not None:
            headers["origin"] = origin
        return SimpleNamespace(url=SimpleNamespace(path=path), headers=headers, scope={"type": "websocket", "headers": []})

    def test_opaque_origin_socket_with_a_capability_is_admitted(self):
        import asyncio

        from app.gateway.routers.sandbox import _ws_admit_preview

        seg = pc.mint_segment(THREAD, "preview", "app", USER)
        # A sandboxed iframe sends Origin: null; the capability is the credential.
        assert asyncio.run(_ws_admit_preview(self._ws(f"/api/sandbox/preview-ws/{seg}/_next/webpack-hmr", "null"))) is USER

    def test_cross_site_socket_without_one_is_still_refused(self):
        import asyncio

        from app.gateway.routers.sandbox import _ws_admit_preview

        assert asyncio.run(_ws_admit_preview(self._ws(f"/api/sandbox/preview-ws/{THREAD}/_next/webpack-hmr", "https://evil.example"))) is None


class TestFramedCors:
    """Fonts and router fetches from an opaque-origin frame are CORS requests (Origin: null)."""

    def _app(self):
        from fastapi.middleware.cors import CORSMiddleware

        from app.gateway.preview_cors import PreviewCapabilityCORSMiddleware

        app = FastAPI()
        app.add_middleware(AuthMiddleware)

        @app.api_route("/api/sandbox/preview/{segment}/{path:path}", methods=["GET", "OPTIONS"])
        async def preview(segment: str, path: str):
            return {"ok": True}

        # Production shape: an allowlist without "null", which 400s its preflight.
        app.add_middleware(CORSMiddleware, allow_origins=["https://nova.example"], allow_credentials=True, allow_methods=["*"], allow_headers=["*"])
        app.add_middleware(PreviewCapabilityCORSMiddleware)
        return TestClient(app)

    def test_font_response_is_readable_from_an_opaque_origin(self):
        seg = pc.mint_segment(THREAD, "preview", "app", USER)
        r = self._app().get(_preview_path(seg, "_next/static/media/a.woff2"), headers={"Origin": "null"})
        assert r.status_code == 200
        assert r.headers["access-control-allow-origin"] == "*"
        assert "access-control-allow-credentials" not in r.headers

    def test_router_preflight_is_answered_before_the_allowlist_rejects_it(self):
        seg = pc.mint_segment(THREAD, "preview", "app", USER)
        r = self._app().options(
            _preview_path(seg, "services"),
            headers={"Origin": "null", "Access-Control-Request-Method": "GET", "Access-Control-Request-Headers": "rsc, next-router-state-tree"},
        )
        assert r.status_code == 204
        assert r.headers["access-control-allow-origin"] == "*"
        assert r.headers["access-control-allow-headers"] == "rsc, next-router-state-tree"

    def test_cookie_authenticated_paths_get_no_cors(self, monkeypatch):
        async def _cookie_user(_request):
            return USER

        monkeypatch.setattr("app.gateway.deps.get_current_user_from_request", _cookie_user)
        client = self._app()
        client.cookies.set("access_token", "session")
        r = client.get(_preview_path(THREAD), headers={"Origin": "https://evil.example"})
        assert "access-control-allow-origin" not in r.headers
