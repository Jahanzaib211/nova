"""A brand-new thread must be able to use its own sandbox endpoints.

The thread *directory* is only materialised when the thread's first **run**
starts, but the Browser panel, the dev-server endpoints, the Files tab and the
Editor all mount the moment the chat page loads. Ownership was decided purely
by directory existence, so for a fresh thread those endpoints answered 404 to
the thread's **own owner** -- the Browser tab never came up, the dev server
could not be started, and the Files tab stayed empty.

The fix is the store fallback that ``ws_caller_owns_thread`` and the SSE log
stream already had: the directory stays the fast path (no database round-trip,
and it covers untracked legacy threads) and the thread's metadata row -- written
at thread creation, long before the directory -- is the fallback.

The assertion that matters most is the negative one. Thread ids are guessable,
so a fallback that degraded into "any id with a row is fine" would be an IDOR
hole: a thread owned by *another* user must still be refused, and a thread id
that exists in neither the store nor the directory must be refused too.

The second half of this module pins the upstream-500 translation: the sandbox's
own port gateway answers 500 with a plain-text Node error
(``connect ECONNREFUSED 0.0.0.0:4100``) when nothing is listening on the port
yet -- the normal state while a dev server boots. The absproxy already turned
that into a friendly 503; the preview and appview surfaces did not, so the
Browser tab rendered a stack-trace-looking line instead of a page that says
the server is not up yet.
"""

from __future__ import annotations

import asyncio
import contextlib
from types import SimpleNamespace

import httpx
import pytest

import nova.sandbox as sandbox_pkg
from app.gateway.routers import sandbox as sandbox_router
from nova.persistence.thread_meta.memory import MemoryThreadMetaStore
from nova.runtime.user_context import reset_current_user, set_current_user

# ── Fixtures ─────────────────────────────────────────────────────────────────


@pytest.fixture(autouse=True)
def _no_disk_thread_dir(tmp_path, monkeypatch):
    """Point the path helpers at a tree where NO thread directory exists.

    This is the whole point of the race: the directory is created by the first
    run, so a thread that has not run yet has a metadata row and no directory.
    Nothing is ever created under ``tmp_path`` -- the directory check must
    answer False on its own.
    """
    root = tmp_path / "threads"

    def _thread_dir(tid, user_id=None):
        return root / str(tid)

    fake_paths = SimpleNamespace(
        thread_dir=_thread_dir,
        sandbox_work_dir=lambda tid, user_id=None: _thread_dir(tid, user_id) / "user-data" / "workspace",
        sandbox_outputs_dir=lambda tid, user_id=None: _thread_dir(tid, user_id) / "user-data" / "outputs",
        sandbox_uploads_dir=lambda tid, user_id=None: _thread_dir(tid, user_id) / "user-data" / "uploads",
        resolve_virtual_path=lambda tid, path, user_id=None: _thread_dir(tid, user_id) / str(path).lstrip("/"),
    )
    monkeypatch.setattr(sandbox_router, "get_paths", lambda: fake_paths)
    # The preview URLs carry a signed capability; a stable secret keeps the
    # test hermetic instead of reaching for the on-disk .jwt_secret.
    monkeypatch.setenv("AUTH_JWT_SECRET", "sandbox-fresh-thread-test-secret-0123456789")
    return root


@pytest.fixture
def store():
    """A real ThreadMetaStore (memory backend) -- the store fallback's authority."""
    from langgraph.store.memory import InMemoryStore

    return MemoryThreadMetaStore(InMemoryStore())


@pytest.fixture
def owned_thread(store):
    """A thread whose metadata row belongs to ``alice``; nobody has run it yet."""
    asyncio.run(store.create("thread-fresh", user_id="alice"))
    return "thread-fresh"


@contextlib.contextmanager
def _as_user(user_id: str):
    """Run a block as ``user_id``.

    The ownership fallback resolves the caller from the same contextvar the
    auth middleware sets, so the identity under test is the real one.
    """
    token = set_current_user(SimpleNamespace(id=user_id))
    try:
        yield
    finally:
        reset_current_user(token)


def _request_for(store):
    """A stand-in for the FastAPI ``Request`` carrying the app state.

    Only ``request.app.state.thread_store`` is consulted by the ownership
    fallback, so this is the whole contract the router has with the request.
    """
    return SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace(thread_store=store)))


class _LocalProvider:
    """A sandbox provider WITHOUT ``get_preview_endpoint``.

    That missing attribute is the duck-type gate the container-sandbox-only
    endpoints check, so they degrade with a reason instead of touching Docker.
    """

    def acquire(self, thread_id: str) -> str:
        return f"aio:{thread_id}"

    def get(self, sandbox_id: str):
        return None


class _ContainerProvider(_LocalProvider):
    """A sandbox provider WITH a preview endpoint -- the absproxy's gate."""

    def get_preview_endpoint(self, thread_id, container_port=4100):
        return ("host.docker.internal", 51000)

    def get(self, sandbox_id: str):
        return SimpleNamespace(base_url="http://host.docker.internal:39123", _client=None)


@pytest.fixture
def no_container_sandbox(monkeypatch):
    """Keep the container-sandbox endpoints away from a real sandbox."""
    monkeypatch.setattr(sandbox_pkg, "get_sandbox_provider", lambda: _LocalProvider())
    monkeypatch.setattr(sandbox_router, "_mark_sandbox_active", lambda _tid: None)

    async def _no_preview(_thread_id):
        return None

    monkeypatch.setattr(sandbox_router, "discover_live_preview", _no_preview)


async def _aget_none(_config):
    return None


# ── FIX 2: the fresh-thread ownership race ───────────────────────────────────


class TestFreshThreadIsServed:
    """A thread with a metadata row and no directory belongs to its owner."""

    def test_dev_status_answers_instead_of_404(self, store, owned_thread, no_container_sandbox):
        request = _request_for(store)
        with _as_user("alice"):
            out = asyncio.run(sandbox_router.dev_status(owned_thread, request))
        assert out["running"] is False
        assert out["status"] == "stopped"

    def test_dev_servers_answers_instead_of_404(self, store, owned_thread):
        request = _request_for(store)
        with _as_user("alice"):
            out = asyncio.run(sandbox_router.dev_servers(owned_thread, request))
        assert out["servers"] == []

    def test_dev_start_answers_instead_of_404(self, store, owned_thread, no_container_sandbox):
        request = _request_for(store)
        with _as_user("alice"):
            out = asyncio.run(sandbox_router.dev_start(owned_thread, request))
        assert out["started"] is False
        assert "container sandbox" in out["reason"]

    def test_dev_external_answers_instead_of_404(self, store, owned_thread, monkeypatch):
        monkeypatch.setattr(sandbox_router, "_is_port_open", lambda host, port, timeout=0.4: True)
        monkeypatch.setattr(
            sandbox_router,
            "register_external_dev_server",
            lambda thread_id, port, *, host="127.0.0.1", label="app", container_port=None: SimpleNamespace(
                thread_id=thread_id,
                label=label,
                host=host,
                port=port,
                status="ready",
            ),
        )
        request = _request_for(store)
        with _as_user("alice"):
            out = asyncio.run(sandbox_router.dev_external(owned_thread, 4321, request, host="127.0.0.1"))
        assert out["status"] == "ready"
        assert out["port"] == 4321

    def test_browser_check_answers_instead_of_404(self, store, owned_thread, no_container_sandbox):
        request = _request_for(store)
        with _as_user("alice"):
            out = asyncio.run(sandbox_router.browser_check(owned_thread, request))
        assert out["ok"] is False
        assert "container sandbox" in out["reason"]

    def test_browser_check_last_answers_instead_of_404(self, store, owned_thread):
        request = _request_for(store)
        with _as_user("alice"):
            out = asyncio.run(sandbox_router.browser_check_last(owned_thread, request))
        assert out["ok"] is None
        assert out["reason"] == "no self-test yet"

    def test_the_file_endpoints_answer_instead_of_404(self, store, owned_thread):
        """The Files tab and the Editor mount with the chat page."""
        request = _request_for(store)
        with _as_user("alice"):
            listing = asyncio.run(sandbox_router.list_sandbox_files(owned_thread, request))
            one = asyncio.run(sandbox_router.get_sandbox_file(owned_thread, "/mnt/user-data/workspace/a.txt", request))
        assert listing["files"] == []
        assert one["exists"] is False

    def test_the_todo_endpoint_answers_instead_of_404(self, store, owned_thread):
        checkpointer = SimpleNamespace(aget_tuple=_aget_none)
        request = _request_for(store)
        with _as_user("alice"):
            out = asyncio.run(sandbox_router.get_sandbox_todo(owned_thread, request, checkpointer=checkpointer))
        assert out["todos"] == []


class TestCrossUserIsStillRefused:
    """The fallback must not become an IDOR hole."""

    @pytest.mark.parametrize(
        "call",
        [
            pytest.param(lambda sb, tid, req: sb.dev_status(tid, req), id="dev-status"),
            pytest.param(lambda sb, tid, req: sb.dev_servers(tid, req), id="dev-servers"),
            pytest.param(lambda sb, tid, req: sb.dev_start(tid, req), id="dev-start"),
            pytest.param(lambda sb, tid, req: sb.browser_check_last(tid, req), id="browser-check-last"),
            pytest.param(lambda sb, tid, req: sb.list_sandbox_files(tid, req), id="files"),
            pytest.param(lambda sb, tid, req: sb.get_sandbox_status(tid, req), id="status"),
        ],
    )
    def test_another_users_thread_is_refused(self, store, owned_thread, no_container_sandbox, call):
        from fastapi import HTTPException

        request = _request_for(store)
        with _as_user("bob"):
            with pytest.raises(HTTPException) as exc_info:
                asyncio.run(call(sandbox_router, owned_thread, request))
        assert exc_info.value.status_code == 404

    def test_another_users_thread_is_refused_for_the_proxies(self, store, owned_thread, monkeypatch):
        """The preview/absproxy/appview surfaces carry a capability in the URL --
        the capability authenticates the *request*, it does not replace the
        ownership check."""
        from fastapi import HTTPException

        monkeypatch.setattr(sandbox_router, "_mark_sandbox_active", lambda _tid: None)
        request = _request_for(store)
        with _as_user("bob"):
            with pytest.raises(HTTPException) as exc_info:
                asyncio.run(sandbox_router._proxy_dev_server(owned_thread, "app", "", request))
        assert exc_info.value.status_code == 404

    def test_a_thread_in_neither_the_store_nor_the_directory_is_refused(self, store, no_container_sandbox):
        """A guessed id with no metadata row stays a denial (``require_existing``)."""
        from fastapi import HTTPException

        request = _request_for(store)
        with _as_user("alice"):
            with pytest.raises(HTTPException) as exc_info:
                asyncio.run(sandbox_router.dev_status("thread-never-existed", request))
        assert exc_info.value.status_code == 404

    def test_a_missing_store_is_a_denial(self, owned_thread, no_container_sandbox):
        """No store means nothing to ask -- a denial, not a crash."""
        from fastapi import HTTPException

        request = SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace(thread_store=None)))
        with _as_user("alice"):
            with pytest.raises(HTTPException) as exc_info:
                asyncio.run(sandbox_router.dev_status(owned_thread, request))
        assert exc_info.value.status_code == 404

    def test_a_request_without_a_store_is_a_denial(self, owned_thread, no_container_sandbox):
        """``request=None`` (the denial tests call the handlers directly) must
        degrade to a denial instead of raising."""
        from fastapi import HTTPException

        with _as_user("alice"):
            with pytest.raises(HTTPException) as exc_info:
                asyncio.run(sandbox_router.dev_status(owned_thread, None))
        assert exc_info.value.status_code == 404

    def test_the_directory_fast_path_still_wins(self, store, tmp_path):
        """A thread whose directory exists is served even without a metadata row
        (legacy threads are not tracked in the store)."""
        thread_id = "thread-legacy"
        (tmp_path / "threads" / thread_id).mkdir(parents=True, exist_ok=True)

        request = _request_for(store)
        with _as_user("alice"):
            out = asyncio.run(sandbox_router.dev_servers(thread_id, request))
        assert out["servers"] == []


# ── FIX 1: the upstream-500 → friendly-503 translation ───────────────────────


def _http_request(path: str = "/"):
    from fastapi import Request

    async def receive():
        return {"type": "http.request", "body": b"", "more_body": False}

    return Request(
        {"type": "http", "method": "GET", "path": path, "headers": [], "query_string": b""},
        receive,
    )


#: Captured BEFORE any monkeypatching: ``httpx.AsyncClient`` is patched per
#: test, and re-capturing it would hand the previous patch's lambda a second
#: ``transport=`` keyword.
_REAL_ASYNC_CLIENT = httpx.AsyncClient


def _mock_upstream(monkeypatch, status: int = 500, content: bytes = b"", headers: dict | None = None):
    """Point the proxies at a canned upstream response.

    A NEW response per request: an ``httpx.Response`` carries a single-shot
    stream, so handing the same instance to two requests would fail on the
    second read instead of proving what the test means to prove.
    """

    def handler(_request):
        return httpx.Response(status, content=content, headers=dict(headers or {}))

    monkeypatch.setattr(
        sandbox_router.httpx,
        "AsyncClient",
        lambda **kw: _REAL_ASYNC_CLIENT(transport=httpx.MockTransport(handler), **kw),
    )


def _ready_handle(label: str = "app"):
    return SimpleNamespace(status="ready", host="dev", port=4100, label=label)


class TestUpstreamRefusedTranslation:
    def test_preview_turns_the_upstream_refusal_into_the_friendly_page(self, monkeypatch):
        monkeypatch.setattr(sandbox_router, "_caller_owns_thread", lambda tid: True)
        monkeypatch.setattr(sandbox_router, "_mark_sandbox_active", lambda _tid: None)
        monkeypatch.setattr(sandbox_router, "get_dev_server", lambda tid, label="app": _ready_handle(label))
        _mock_upstream(
            monkeypatch,
            status=500,
            content=b"connect ECONNREFUSED 0.0.0.0:4100",
            headers={"x-upstream-marker": "must-not-leak"},
        )

        resp = asyncio.run(sandbox_router._proxy_dev_server("t1", "app", "", _http_request()))

        assert resp.status_code == 503
        assert b"Nothing on that port yet" in resp.body
        # The upstream's own headers describe the error body we are NOT serving.
        assert "x-upstream-marker" not in resp.headers

    def test_appview_turns_the_upstream_refusal_into_the_friendly_page(self, monkeypatch):
        monkeypatch.setattr(sandbox_router, "_caller_owns_thread", lambda tid: True)

        async def _base_url(_thread_id):
            return "http://sandbox.local:8080"

        monkeypatch.setattr(sandbox_router, "_sandbox_base_url", _base_url)
        _mock_upstream(
            monkeypatch,
            status=500,
            content=b"connect ECONNREFUSED 0.0.0.0:4100",
            headers={"x-upstream-marker": "must-not-leak"},
        )

        resp = asyncio.run(sandbox_router._proxy_appview("t1", "terminal", _http_request()))

        assert resp.status_code == 503
        assert b"Nothing on that port yet" in resp.body
        assert "x-upstream-marker" not in resp.headers

    def test_a_large_upstream_error_page_passes_through_unchanged(self, monkeypatch):
        """The match stays narrow: an app's own error page that merely mentions
        "connection refused" must reach the browser untouched."""
        monkeypatch.setattr(sandbox_router, "_caller_owns_thread", lambda tid: True)
        monkeypatch.setattr(sandbox_router, "_mark_sandbox_active", lambda _tid: None)
        monkeypatch.setattr(sandbox_router, "get_dev_server", lambda tid, label="app": _ready_handle(label))
        body = b"ECONNREFUSED " + (b"x" * 600)
        _mock_upstream(monkeypatch, status=500, content=body)

        resp = asyncio.run(sandbox_router._proxy_dev_server("t1", "app", "", _http_request()))

        assert resp.status_code == 500
        assert resp.body == body

    def test_all_three_proxies_answer_the_same_way(self, monkeypatch):
        """One wording for "nothing is listening yet" across every surface."""
        monkeypatch.setattr(sandbox_router, "_caller_owns_thread", lambda tid: True)
        monkeypatch.setattr(sandbox_router, "_mark_sandbox_active", lambda _tid: None)
        monkeypatch.setattr(sandbox_router, "get_dev_server", lambda tid, label="app": _ready_handle(label))
        monkeypatch.setattr(sandbox_pkg, "get_sandbox_provider", lambda: _ContainerProvider())

        async def _base_url(_thread_id):
            return "http://sandbox.local:8080"

        monkeypatch.setattr(sandbox_router, "_sandbox_base_url", _base_url)

        bodies = []
        for call in (
            lambda: sandbox_router._proxy_dev_server("t1", "app", "", _http_request()),
            lambda: sandbox_router._absproxy_impl("t1", 4321, "", _http_request()),
            lambda: sandbox_router._proxy_appview("t1", "terminal", _http_request()),
        ):
            _mock_upstream(monkeypatch, status=500, content=b"connect ECONNREFUSED 0.0.0.0:4100")
            resp = asyncio.run(call())
            assert resp.status_code == 503
            bodies.append(resp.body)

        assert bodies[0] == bodies[1] == bodies[2]
        assert bodies[0].decode() == sandbox_router._NOTHING_ON_PORT_HTML
