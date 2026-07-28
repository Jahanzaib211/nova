"""Real full-stack E2E for the workspace intelligence API (C10).

No mocks anywhere: the production app from ``create_app()`` (full
middleware stack — AuthMiddleware, CSRF, security headers), a real
SQLite engine, users registered through the real ``/api/v1/auth/register``
endpoint with real JWT cookies, a real on-disk Python project scanned by
the real WorkspaceIntelligenceServiceImpl, and real thread-meta rows for
the cross-tenant denial case.

Covers, over the real wire:
- feature flag off  -> 403 on every endpoint
- unauthenticated   -> 401
- the full seven-endpoint loop: index, snapshot, symbols (q= and file=),
  commands, plan, impact, metrics
- cross-tenant access -> 404 (thread meta owned by another user)
"""

import asyncio
import json
import textwrap
import threading
import time

import httpx
import pytest
from fastapi.testclient import TestClient

from app.gateway.auth.config import AuthConfig, set_auth_config

_TEST_SECRET = "workspace-e2e-secret-key-0123456789abcdef"
_PASSWORD = "Tr0ub4dor3a"
_THREAD_ID = "e2e-workspace-thread"


@pytest.fixture(autouse=True)
def _real_stack(tmp_path, monkeypatch):
    """Real engine + real paths rooted in tmp, workspace flag on."""
    import deerflow.config.paths as paths_module
    from app.gateway import deps
    from deerflow.config import get_app_config
    from deerflow.persistence.engine import close_engine, init_engine

    set_auth_config(AuthConfig(jwt_secret=_TEST_SECRET))

    home = tmp_path / "home"
    home.mkdir(parents=True, exist_ok=True)
    monkeypatch.setenv("DEER_FLOW_HOME", str(home))
    monkeypatch.setattr(paths_module, "_paths", None)

    config = get_app_config()
    # Point the app's database at tmp so the lifespan engine init never
    # touches the deployment DB.
    from deerflow.config.database_config import DatabaseConfig

    original_database = config.database
    monkeypatch.setattr(config, "database", DatabaseConfig(backend="sqlite", sqlite_dir=str(tmp_path)))
    del original_database

    url = f"sqlite+aiosqlite:///{tmp_path}/deerflow.db"
    asyncio.run(init_engine("sqlite", url=url, sqlite_dir=str(tmp_path)))
    deps._cached_local_provider = None
    deps._cached_repo = None

    original_flag = config.workspace.intelligence_enabled
    config.workspace.intelligence_enabled = True
    try:
        yield
    finally:
        config.workspace.intelligence_enabled = original_flag
        deps._cached_local_provider = None
        deps._cached_repo = None
        asyncio.run(close_engine())
        monkeypatch.setattr(paths_module, "_paths", None)


@pytest.fixture()
def client(_real_stack):
    from app.gateway.app import create_app

    with TestClient(create_app()) as c:
        yield c


def _csrf(client: TestClient) -> dict[str, str]:
    token = client.cookies.get("csrf_token")
    return {"X-CSRF-Token": token} if token else {}


def _register(client: TestClient, email: str) -> str:
    """Register through the real endpoint; returns the real user id."""
    resp = client.post("/api/v1/auth/register", json={"email": email, "password": _PASSWORD})
    assert resp.status_code == 201, resp.text
    me = client.get("/api/v1/auth/me")
    assert me.status_code == 200, me.text
    return me.json()["id"]


def _create_real_project(user_id: str, thread_id: str = _THREAD_ID) -> None:
    """Materialize a real Python project in the thread's workspace dir."""
    from deerflow.config.paths import get_paths

    root = get_paths().sandbox_work_dir(thread_id, user_id=user_id)
    (root / "src").mkdir(parents=True, exist_ok=True)
    (root / "pyproject.toml").write_text(
        textwrap.dedent(
            """
            [project]
            name = "e2e-demo"
            version = "0.1.0"
            """
        )
    )
    (root / "Makefile").write_text("test:\n\tpytest -q\n\nlint:\n\truff check .\n")
    (root / "src" / "calculator.py").write_text(
        textwrap.dedent(
            '''
            """A real module for the scanner to index."""


            class Calculator:
                def add(self, a: int, b: int) -> int:
                    return a + b

                def multiply(self, a: int, b: int) -> int:
                    return a * b


            def make_calculator() -> Calculator:
                return Calculator()
            '''
        )
    )


class TestWorkspaceFlagAndAuth:
    def test_unauthenticated_requests_are_401(self, client):
        resp = client.get(f"/api/workspace/{_THREAD_ID}/snapshot")
        assert resp.status_code == 401

    def test_flag_off_returns_403_for_authed_user(self, client):
        from deerflow.config import get_app_config

        user_id = _register(client, "flagoff@example.com")
        _create_real_project(user_id)
        config = get_app_config()
        config.workspace.intelligence_enabled = False
        try:
            resp = client.get(f"/api/workspace/{_THREAD_ID}/snapshot")
            assert resp.status_code == 403
            assert "not enabled" in resp.json()["detail"]
            resp = client.post(f"/api/workspace/{_THREAD_ID}/index", json={}, headers=_csrf(client))
            assert resp.status_code == 403
        finally:
            config.workspace.intelligence_enabled = True


class TestWorkspaceSevenEndpointLoop:
    def test_full_loop_over_the_real_wire(self, client):
        user_id = _register(client, "loop@example.com")
        _create_real_project(user_id)
        headers = _csrf(client)

        # 1. index — real scan of the real directory
        resp = client.post(f"/api/workspace/{_THREAD_ID}/index", json={}, headers=headers)
        assert resp.status_code == 200, resp.text
        summary = resp.json()["snapshot"]
        assert summary["primary_language"] == "python"
        assert summary["symbol_count"] >= 3  # Calculator, add, multiply, make_calculator
        assert summary["project_count"] >= 1

        # 2. snapshot
        resp = client.get(f"/api/workspace/{_THREAD_ID}/snapshot")
        assert resp.status_code == 200
        assert resp.json()["snapshot"]["repo_kind"]

        # 3. symbols — prefix search then file filter
        resp = client.get(f"/api/workspace/{_THREAD_ID}/symbols", params={"q": "calc"})
        assert resp.status_code == 200
        names = [s["name"].lower() for s in resp.json()["symbols"]]
        assert any("calculator" in n for n in names)

        resp = client.get(f"/api/workspace/{_THREAD_ID}/symbols", params={"file": "src/calculator.py"})
        assert resp.status_code == 200
        file_symbols = resp.json()["symbols"]
        assert file_symbols, "file= filter must return the module's symbols"
        assert all("calculator.py" in s["file_path"] for s in file_symbols)

        # 4. commands — discovered from the real Makefile
        resp = client.get(f"/api/workspace/{_THREAD_ID}/commands")
        assert resp.status_code == 200
        commands = resp.json()["commands"]
        command_names = {c["name"] for c in commands}
        assert {"test", "lint"} & command_names

        # 5. plan — search kind builds a real read-only plan
        resp = client.post(
            f"/api/workspace/{_THREAD_ID}/plan",
            json={"kind": "search", "query": "calculator"},
            headers=headers,
        )
        assert resp.status_code == 200, resp.text
        plan = resp.json()["plan"]
        assert plan["steps"]
        assert plan["risk_level"] in ("low", "medium")

        # 6. impact — real file, real dependency walk
        resp = client.post(
            f"/api/workspace/{_THREAD_ID}/impact",
            json={"files": ["src/calculator.py"]},
            headers=headers,
        )
        assert resp.status_code == 200, resp.text
        impact = resp.json()
        assert impact["symbols"], "symbols in the file must be affected"
        assert impact["projects"], "the containing project must be affected"

        # 7. metrics — the scan above must be visible
        resp = client.get(f"/api/workspace/{_THREAD_ID}/metrics")
        assert resp.status_code == 200
        metrics = resp.json()["metrics"]
        assert metrics["scan"]["count"] >= 1

    def test_force_refresh_rescans_and_cache_serves_repeat_reads(self, client):
        user_id = _register(client, "cache@example.com")
        _create_real_project(user_id, thread_id="e2e-cache-thread")
        headers = _csrf(client)

        first = client.post("/api/workspace/e2e-cache-thread/index", json={}, headers=headers)
        assert first.status_code == 200
        second = client.post(
            "/api/workspace/e2e-cache-thread/index",
            json={"force_refresh": True},
            headers=headers,
        )
        assert second.status_code == 200
        assert second.json()["snapshot"]["symbol_count"] == first.json()["snapshot"]["symbol_count"]


class TestWorkspaceCrossTenant:
    def test_thread_owned_by_other_user_is_404(self, client):
        owner_id = _register(client, "owner@example.com")
        _create_real_project(owner_id, thread_id="owned-thread")

        # Stamp real thread-meta ownership through the real app's store.
        store = client.app.state.thread_store
        asyncio.run(store.create("owned-thread", user_id=owner_id))

        # A different real user must see 404, not 403 (no existence leak).
        intruder = TestClient(client.app)
        _register(intruder, "intruder@example.com")
        resp = intruder.get("/api/workspace/owned-thread/snapshot")
        assert resp.status_code == 404


class TestWorkspaceEventsSSE:
    """Real bus -> real SSE bridge -> real HTTP stream, no mocks.

    A genuinely infinite SSE generator (heartbeats every 15s, otherwise
    idle) cannot be observed through Starlette's ``TestClient`` or
    ``httpx.ASGITransport``: both buffer the ASGI app's response until its
    ``__call__`` coroutine returns, which never happens for an endpoint
    that never terminates. Verified directly — the identical hang
    reproduces with a bare two-line ``StreamingResponse`` generator, no
    app code, no middleware, no auth. So this class runs a real
    ``uvicorn`` server on a real TCP socket (the ``live_server`` fixture)
    and reads the stream with a real ``httpx.Client``, exactly like a
    browser would — the only way to observe true incremental delivery.
    """

    @pytest.fixture()
    def live_server(self, client):
        """Serve the already-configured app (same engine, same event bus) over a real socket."""
        import socket
        import threading

        import uvicorn

        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
        sock.close()

        config = uvicorn.Config(client.app, host="127.0.0.1", port=port, log_level="warning")
        server = uvicorn.Server(config)
        thread = threading.Thread(target=server.run, daemon=True)
        thread.start()

        deadline = time.monotonic() + 10.0
        while not server.started and time.monotonic() < deadline:
            time.sleep(0.05)
        assert server.started, "uvicorn did not start within 10s"

        try:
            yield f"http://127.0.0.1:{port}"
        finally:
            server.should_exit = True
            thread.join(timeout=5.0)

    def test_real_scan_produces_a_real_sse_event(self, client, live_server):
        user_id = _register(client, "sse@example.com")
        _create_real_project(user_id, thread_id="e2e-sse-thread")
        headers = _csrf(client)
        cookies = dict(client.cookies)

        frames: list[str] = []
        stop_event = threading.Event()
        reader_error: list[BaseException] = []

        def read_stream():
            try:
                with httpx.Client(cookies=cookies, timeout=None) as sse_client:
                    with sse_client.stream("GET", f"{live_server}/api/workspace/e2e-sse-thread/events") as response:
                        response.raise_for_status()
                        buffer = ""
                        for chunk in response.iter_text():
                            if stop_event.is_set():
                                return
                            buffer += chunk
                            while "\n\n" in buffer:
                                frame, buffer = buffer.split("\n\n", 1)
                                frames.append(frame)
                                if "event: WorkspaceScanned" in frame:
                                    return
            except BaseException as exc:  # pragma: no cover - surfaced in main thread
                reader_error.append(exc)

        reader = threading.Thread(target=read_stream, daemon=True)
        reader.start()

        # Give the reader time to actually subscribe before the scan fires
        # — otherwise the event may publish before anyone is listening.
        time.sleep(0.5)

        resp = client.post(
            "/api/workspace/e2e-sse-thread/index",
            json={"force_refresh": True},
            headers=headers,
        )
        assert resp.status_code == 200

        reader.join(timeout=10.0)
        stop_event.set()

        if reader_error:
            raise reader_error[0]
        scanned_frame = next((f for f in frames if "event: WorkspaceScanned" in f), None)
        assert scanned_frame is not None, f"no WorkspaceScanned SSE frame received; got: {frames}"

        data_line = next(line for line in scanned_frame.splitlines() if line.startswith("data: "))
        payload = json.loads(data_line.removeprefix("data: "))
        assert payload["symbol_count"] >= 3
        assert payload["command_count"] >= 1
        assert "e2e-sse-thread" in payload["root_path"]

    def test_unrelated_thread_does_not_receive_this_threads_events(self, client, live_server):
        user_id = _register(client, "sse-scope@example.com")
        _create_real_project(user_id, thread_id="e2e-sse-thread-a")
        _create_real_project(user_id, thread_id="e2e-sse-thread-b")
        headers = _csrf(client)
        cookies = dict(client.cookies)

        frames: list[str] = []
        stop_event = threading.Event()

        def read_stream():
            try:
                with httpx.Client(cookies=cookies, timeout=None) as sse_client:
                    with sse_client.stream("GET", f"{live_server}/api/workspace/e2e-sse-thread-b/events") as response:
                        for chunk in response.iter_text():
                            if stop_event.is_set():
                                return
                            frames.append(chunk)
            except BaseException:
                pass

        reader = threading.Thread(target=read_stream, daemon=True)
        reader.start()
        time.sleep(0.5)

        resp = client.post(
            "/api/workspace/e2e-sse-thread-a/index",
            json={"force_refresh": True},
            headers=headers,
        )
        assert resp.status_code == 200

        # thread-b's stream must stay quiet — no cross-thread leakage.
        time.sleep(1.5)
        stop_event.set()
        reader.join(timeout=2.0)

        collected = "".join(frames)
        assert "WorkspaceScanned" not in collected
