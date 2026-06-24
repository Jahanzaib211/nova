"""Tests for the AIO/container dev-server preview path.

Covers the additive preview-port plumbing (SandboxInfo.preview_ports, the
LocalContainerBackend extra-port publishing, AioSandboxProvider.get_preview_endpoint)
and the dev_server registry's AIO branch (in-container backgrounded launch +
(thread, label) keying). The local host-subprocess branch is unchanged and
selected when ``sandbox=None``.
"""

from __future__ import annotations

import asyncio
import importlib
import json
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

# Import the agent graph first so deerflow.sandbox.tools loads in the same order
# as production (avoids a tools-first circular import with deerflow.agents).
import deerflow.agents  # noqa: F401,E402  (import-order priming)
from deerflow.community.aio_sandbox.local_backend import LocalContainerBackend
from deerflow.community.aio_sandbox.sandbox_info import SandboxInfo


# ── SandboxInfo.preview_ports ────────────────────────────────────────────────


def test_sandbox_info_preview_ports_roundtrip():
    info = SandboxInfo(
        sandbox_id="abc",
        sandbox_url="http://host.docker.internal:8080",
        preview_ports={4100: 51000, 4101: 51001},
    )
    data = info.to_dict()
    assert data["preview_ports"] == {4100: 51000, 4101: 51001}

    # JSON serialization turns int keys into strings; from_dict must coerce back.
    revived = SandboxInfo.from_dict(json.loads(json.dumps(data)))
    assert revived.preview_ports == {4100: 51000, 4101: 51001}


def test_sandbox_info_preview_ports_default_empty():
    info = SandboxInfo(sandbox_id="abc", sandbox_url="http://x:8080")
    assert info.preview_ports == {}
    assert SandboxInfo.from_dict({"sandbox_id": "a", "sandbox_url": "u"}).preview_ports == {}


# ── LocalContainerBackend preview-port publishing ────────────────────────────


def _fake_get_free_port_factory(start=51000):
    state = {"n": start}

    def _fake(start_port=8080, max_range=100):
        port = state["n"]
        state["n"] += 1
        return port

    return _fake


def test_start_container_publishes_preview_ports(monkeypatch):
    backend = LocalContainerBackend(
        image="sandbox:latest",
        base_port=8080,
        container_prefix="sandbox",
        config_mounts=[],
        environment={},
        preview_container_ports=[4100, 4101],
    )
    monkeypatch.setattr(backend, "_runtime", "docker")
    monkeypatch.setattr(
        "deerflow.community.aio_sandbox.local_backend.get_free_port",
        _fake_get_free_port_factory(51000),
    )

    captured: list[str] = []

    def fake_run(cmd, **kwargs):
        captured.extend(cmd)
        return SimpleNamespace(stdout="container-id\n", stderr="", returncode=0)

    monkeypatch.setattr("subprocess.run", fake_run)

    container_id, preview_ports = backend._start_container("sandbox-test", 18080)

    assert container_id == "container-id"
    assert preview_ports == {4100: 51000, 4101: 51001}
    joined = " ".join(captured)
    assert ":18080:8080" in joined  # main API port still published
    assert ":51000:4100" in joined  # preview port published
    assert ":51001:4101" in joined


def test_batch_inspect_extracts_preview_ports(monkeypatch):
    """Adopted/reconciled containers must keep their published preview ports."""
    backend = LocalContainerBackend(
        image="sandbox:latest",
        base_port=8080,
        container_prefix="deer-flow-sandbox",
        config_mounts=[],
        environment={},
        preview_container_ports=[4100, 4101],
    )
    inspect_payload = json.dumps([
        {
            "Name": "/deer-flow-sandbox-abc",
            "Created": "2026-06-22T01:00:00.000000000Z",
            "NetworkSettings": {
                "Ports": {
                    "8080/tcp": [{"HostIp": "0.0.0.0", "HostPort": "18080"}],
                    "4100/tcp": [{"HostIp": "0.0.0.0", "HostPort": "51000"}],
                    "4101/tcp": [{"HostIp": "0.0.0.0", "HostPort": "51001"}],
                }
            },
        }
    ])

    def fake_run(cmd, **kwargs):
        return SimpleNamespace(stdout=inspect_payload, stderr="", returncode=0)

    monkeypatch.setattr("subprocess.run", fake_run)
    out = backend._batch_inspect(["deer-flow-sandbox-abc"])
    created_at, host_port, preview_ports = out["deer-flow-sandbox-abc"]
    assert host_port == 18080
    assert preview_ports == {4100: 51000, 4101: 51001}


def test_start_container_no_preview_ports_by_default(monkeypatch):
    backend = LocalContainerBackend(
        image="sandbox:latest",
        base_port=8080,
        container_prefix="sandbox",
        config_mounts=[],
        environment={},
    )
    monkeypatch.setattr(backend, "_runtime", "docker")
    captured: list[str] = []

    def fake_run(cmd, **kwargs):
        captured.extend(cmd)
        return SimpleNamespace(stdout="cid\n", stderr="", returncode=0)

    monkeypatch.setattr("subprocess.run", fake_run)

    container_id, preview_ports = backend._start_container("sandbox-test", 18080)
    assert preview_ports == {}
    # Only the 8080 mapping should be present.
    assert " ".join(captured).count(":8080") == 1


# ── AioSandboxProvider.get_preview_endpoint ──────────────────────────────────


def _make_bare_provider():
    aio_mod = importlib.import_module("deerflow.community.aio_sandbox.aio_sandbox_provider")
    provider = aio_mod.AioSandboxProvider.__new__(aio_mod.AioSandboxProvider)
    provider._lock = MagicMock()
    provider._thread_sandboxes = {}
    provider._sandbox_infos = {}
    provider._warm_pool = {}
    return provider


def test_get_preview_endpoint_resolves_host_and_port():
    provider = _make_bare_provider()
    provider._thread_sandboxes["thread-1"] = "sb-1"
    provider._sandbox_infos["sb-1"] = SandboxInfo(
        sandbox_id="sb-1",
        sandbox_url="http://host.docker.internal:8080",
        preview_ports={4100: 51000},
    )

    assert provider.get_preview_endpoint("thread-1", 4100) == ("host.docker.internal", 51000)


def test_get_preview_endpoint_none_when_unknown():
    provider = _make_bare_provider()
    assert provider.get_preview_endpoint("nope", 4100) is None

    # Known thread but the requested preview port was not published.
    provider._thread_sandboxes["t"] = "sb"
    provider._sandbox_infos["sb"] = SandboxInfo(sandbox_id="sb", sandbox_url="http://h:8080")
    assert provider.get_preview_endpoint("t", 4100) is None


# ── dev_server AIO branch ────────────────────────────────────────────────────


class _FakeSandbox:
    def __init__(self):
        self.id = "sb-1"
        self.commands: list[str] = []

    def execute_command(self, command: str) -> str:
        self.commands.append(command)
        return "deerflow-dev-started"

    def read_file(self, path: str) -> str:
        return "Error: not found"


def test_dev_server_aio_branch_runs_in_container(monkeypatch):
    dev_server = importlib.import_module("deerflow.sandbox.dev_server")
    dev_server._servers.clear()

    fake_provider = SimpleNamespace(
        get_preview_endpoint=lambda thread_id, container_port=4100: ("host.docker.internal", 51000)
    )
    monkeypatch.setattr("deerflow.sandbox.get_sandbox_provider", lambda: fake_provider)
    # Avoid touching the real filesystem when mirroring to sandbox.log.
    monkeypatch.setattr(dev_server, "_append_devlog_to_sandbox_log", lambda *a, **k: None)

    sandbox = _FakeSandbox()

    async def _run():
        handle = await dev_server.start_dev_server(
            "thread-1", "/mnt/user-data/workspace/app", "npm run dev", sandbox=sandbox
        )
        await dev_server.stop_dev_server("thread-1")
        return handle

    handle = asyncio.run(_run())

    assert handle.host == "host.docker.internal"
    assert handle.port == 51000
    assert handle.container_port == 4100
    # The dev server is launched backgrounded inside the container as a process
    # group (setsid) so stop can reap the whole tree. Env is applied via `env`
    # AFTER setsid (never `setsid PORT=…`, which would exec the assignment).
    launch = sandbox.commands[0]
    assert "setsid env PORT=4100" in launch
    assert "setsid PORT=" not in launch
    assert "HOSTNAME=0.0.0.0" in launch
    assert "/mnt/user-data/workspace/app" in launch
    # stop_dev_server kills the whole process GROUP (negative pid) + frees the port.
    stop_cmd = next(c for c in sandbox.commands if "kill" in c)
    assert 'kill -TERM -"$pid"' in stop_cmd
    assert "fuser -k 4100/tcp" in stop_cmd


def test_dev_server_aio_branch_errors_without_endpoint(monkeypatch):
    dev_server = importlib.import_module("deerflow.sandbox.dev_server")
    dev_server._servers.clear()

    fake_provider = SimpleNamespace(get_preview_endpoint=lambda thread_id, container_port=4100: None)
    monkeypatch.setattr("deerflow.sandbox.get_sandbox_provider", lambda: fake_provider)

    sandbox = _FakeSandbox()

    async def _run():
        return await dev_server.start_dev_server("thread-x", "/mnt/user-data/workspace", sandbox=sandbox)

    handle = asyncio.run(_run())
    assert handle.status == "error"
    assert sandbox.commands == []  # never attempted to launch


def test_ensure_host_binding():
    from deerflow.sandbox.dev_server import _ensure_host_binding

    # Next.js: no flag added (HOSTNAME env handles it).
    assert _ensure_host_binding("next dev") == "next dev"
    # Vite direct + via package managers gets a host flag.
    assert _ensure_host_binding("vite") == "vite --host 0.0.0.0"
    assert _ensure_host_binding("npm run dev") == "npm run dev"  # unknown framework, env-covered
    assert _ensure_host_binding("pnpm vite") == "pnpm vite -- --host 0.0.0.0"
    # Already-bound commands are left untouched.
    assert _ensure_host_binding("vite --host 1.2.3.4") == "vite --host 1.2.3.4"
    assert _ensure_host_binding("next dev -H 0.0.0.0") == "next dev -H 0.0.0.0"


def test_port_alive_reflects_listening_socket():
    """dev-status liveness is judged by a real TCP connect, so a running server's
    preview persists even if the log-tail poller died."""
    from app.gateway.routers.sandbox import _port_alive

    async def _run():
        server = await asyncio.start_server(lambda r, w: w.close(), "127.0.0.1", 0)
        port = server.sockets[0].getsockname()[1]
        try:
            alive = await _port_alive("127.0.0.1", port)
        finally:
            server.close()
            await server.wait_closed()
        # After close, the port is no longer listening.
        dead = await _port_alive("127.0.0.1", port, timeout=0.2)
        return alive, dead

    alive, dead = asyncio.run(_run())
    assert alive is True
    assert dead is False


def test_extract_dev_command_strips_leading_env_prefix():
    from deerflow.sandbox.tools import _extract_dev_command_and_cwd

    # A leading inline env-assignment (PORT=4200 …) must be stripped so the
    # launcher doesn't exec it as the program (setsid: failed to execute PORT=…).
    cmd, cwd = _extract_dev_command_and_cwd("cd /mnt/user-data/workspace && PORT=4200 npm run dev", "/root/ws")
    assert cmd == "npm run dev"
    assert cwd == "/root/ws"

    cmd2, _ = _extract_dev_command_and_cwd("PORT=4200 HOST=0.0.0.0 next dev", "/root/ws")
    assert cmd2 == "next dev"

    # No env prefix → unchanged.
    cmd3, _ = _extract_dev_command_and_cwd("npm run dev", "/root/ws")
    assert cmd3 == "npm run dev"


def test_looks_like_dev_server_matches_dev_and_serves():
    from deerflow.sandbox.tools import _looks_like_dev_server

    for cmd in (
        "npm run dev",
        "npm start",
        "npm run start",
        "next dev",
        "next start",
        "npx next start -p 4100",
        "pnpm dev",
        "vite",
        "serve out -p 4100",
        "npx http-server -p 4100",
        "python -m http.server 4100",
        "python3 -m http.server 4100",
    ):
        assert _looks_like_dev_server(cmd), f"expected dev-server match: {cmd!r}"

    for cmd in ("npm run build", "next build", "npm run lint", "npm test"):
        assert not _looks_like_dev_server(cmd), f"should NOT match: {cmd!r}"


def test_thread_id_for_observation_local_and_aio(monkeypatch):
    from deerflow.sandbox import tools as sandbox_tools

    # Local per-thread id encodes the thread directly.
    assert sandbox_tools._thread_id_for_observation("local:abc-123") == "abc-123"
    # Legacy global / empty → None.
    assert sandbox_tools._thread_id_for_observation("local") is None
    assert sandbox_tools._thread_id_for_observation("") is None

    # AIO hash id → reverse-looked-up from the provider's thread→sandbox map.
    fake_provider = SimpleNamespace(_thread_sandboxes={"thread-9": "hash-9", "thread-8": "hash-8"})
    monkeypatch.setattr("deerflow.sandbox.get_sandbox_provider", lambda: fake_provider)
    assert sandbox_tools._thread_id_for_observation("hash-9") == "thread-9"
    assert sandbox_tools._thread_id_for_observation("unknown-hash") is None


def test_allocate_container_port_assigns_distinct_ports(monkeypatch):
    dev_server = importlib.import_module("deerflow.sandbox.dev_server")
    dev_server._servers.clear()

    # Default label always gets the first published port.
    assert dev_server.allocate_container_port("t", "app") == 4100

    # Register an app server on 4100, then a new label takes the next free port.
    dev_server._servers[dev_server._server_key("t", "app")] = dev_server.DevServerHandle(
        thread_id="t", port=0, cwd="/x", command="npm run dev", label="app", container_port=4100
    )
    assert dev_server.allocate_container_port("t", "api") == 4101
    dev_server._servers[dev_server._server_key("t", "api")] = dev_server.DevServerHandle(
        thread_id="t", port=0, cwd="/x", command="npm run dev", label="api", container_port=4101
    )
    assert dev_server.allocate_container_port("t", "worker") == 4102
    dev_server._servers.clear()


def test_dev_server_label_keying(monkeypatch):
    dev_server = importlib.import_module("deerflow.sandbox.dev_server")
    dev_server._servers.clear()
    monkeypatch.setattr(dev_server, "_append_devlog_to_sandbox_log", lambda *a, **k: None)

    fake_provider = SimpleNamespace(
        get_preview_endpoint=lambda thread_id, container_port=4100: ("h", 50000 + container_port)
    )
    monkeypatch.setattr("deerflow.sandbox.get_sandbox_provider", lambda: fake_provider)
    sandbox = _FakeSandbox()

    async def _run():
        await dev_server.start_dev_server("t", "/mnt/user-data/workspace", sandbox=sandbox, label="app", container_port=4100)
        await dev_server.start_dev_server("t", "/mnt/user-data/workspace/api", sandbox=sandbox, label="api", container_port=4101)
        app = dev_server.get_dev_server("t", "app")
        api = dev_server.get_dev_server("t", "api")
        servers = dev_server.list_dev_servers("t")
        await dev_server.stop_dev_server("t", "app")
        await dev_server.stop_dev_server("t", "api")
        return app, api, servers

    app, api, servers = asyncio.run(_run())
    assert app is not None and app.label == "app" and app.container_port == 4100
    assert api is not None and api.label == "api" and api.container_port == 4101
    assert len(servers) == 2
