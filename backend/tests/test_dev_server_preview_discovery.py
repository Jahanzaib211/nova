"""Preview discovery/adoption must rescue a stale or missing dev-server handle.

Regression: the default ``app`` handle was bound to container port 4100 (host
4101) while the live dev server actually ran on container port 4101 (host 4102),
so ``_proxy_dev_server`` 503'd on a server that was perfectly healthy — the
preview showed once after `dev_start`, then went blank for the rest of the
session. The discovery path probes every published preview port with the same
TCP liveness authority the status endpoint uses and adopts a fresh handle, and
``has_live_dev_server`` keeps the sandbox idle reaper from killing an
in-use preview.
"""

from __future__ import annotations

import socket

import pytest

import deerflow.sandbox.dev_server as ds

_THREAD = "thread-disc-1"


@pytest.fixture(autouse=True)
def _clean_registry():
    ds._servers.clear()
    yield
    ds._servers.clear()


class _Listener:
    """Binds a real 127.0.0.1 socket so TCP liveness is exercised truthfully."""

    def __init__(self):
        self.sock = socket.socket()
        self.sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self.sock.bind(("127.0.0.1", 0))
        self.sock.listen(1)

    @property
    def port(self) -> int:
        return self.sock.getsockname()[1]

    def close(self):
        self.sock.close()


@pytest.mark.anyio
async def test_open_port_is_alive() -> None:
    listener = _Listener()
    try:
        assert await ds.port_alive("127.0.0.1", listener.port) is True
    finally:
        listener.close()


@pytest.mark.anyio
async def test_closed_port_is_dead() -> None:
    # Find a free port, then close the probe socket — nothing is listening.
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
    assert await ds.port_alive("127.0.0.1", port) is False


@pytest.mark.anyio
async def test_empty_input_is_dead() -> None:
    assert await ds.port_alive("", 0) is False
    assert await ds.port_alive("127.0.0.1", 0) is False


class _EndpointProvider:
    """Fake provider whose get_preview_endpoint returns a canned mapping."""

    def __init__(self, mapping: dict[int, tuple[str, int] | None]):
        self.mapping = mapping

    def get_preview_endpoint(self, thread_id: str, container_port: int = 4100):
        return self.mapping.get(container_port)


@pytest.mark.anyio
async def test_discover_picks_first_live_published_port(monkeypatch) -> None:
    import deerflow.sandbox as sandbox_pkg

    provider = _EndpointProvider({4100: ("host.docker.internal", 4101), 4101: ("host.docker.internal", 4102)})
    monkeypatch.setattr(sandbox_pkg, "get_sandbox_provider", lambda: provider)
    live = {4101: True, 4102: False}

    async def fake_port_alive(host, port, timeout=0.4):
        return live.get(port, False)

    monkeypatch.setattr(ds, "port_alive", fake_port_alive)
    found = await ds.discover_live_preview(_THREAD)
    assert found == (4100, "host.docker.internal", 4101)


@pytest.mark.anyio
async def test_discover_skips_dead_ports(monkeypatch) -> None:
    import deerflow.sandbox as sandbox_pkg

    provider = _EndpointProvider({4100: ("h", 9001), 4101: ("h", 9002), 4102: ("h", 9003)})
    monkeypatch.setattr(sandbox_pkg, "get_sandbox_provider", lambda: provider)
    live = {9001: False, 9002: True, 9003: True}

    async def fake_port_alive(host, port, timeout=0.4):
        return live.get(port, False)

    monkeypatch.setattr(ds, "port_alive", fake_port_alive)
    found = await ds.discover_live_preview(_THREAD)
    assert found == (4101, "h", 9002)


@pytest.mark.anyio
async def test_discover_returns_none_when_nothing_listens(monkeypatch) -> None:
    import deerflow.sandbox as sandbox_pkg

    provider = _EndpointProvider({4100: ("h", 1), 4101: None})
    monkeypatch.setattr(sandbox_pkg, "get_sandbox_provider", lambda: provider)

    async def fake_port_alive(host, port, timeout=0.4):
        return False

    monkeypatch.setattr(ds, "port_alive", fake_port_alive)
    assert await ds.discover_live_preview(_THREAD) is None


@pytest.mark.anyio
async def test_discover_no_provider_degrades_to_none(monkeypatch) -> None:
    import deerflow.sandbox as sandbox_pkg

    monkeypatch.setattr(sandbox_pkg, "get_sandbox_provider", lambda: object())
    assert await ds.discover_live_preview(_THREAD) is None


class TestAdoptHandle:
    def test_registers_a_ready_handle(self) -> None:
        handle = ds.adopt_handle(_THREAD, "app", 4101, "host.docker.internal", 4102)
        assert handle.status == "ready"
        assert handle.port == 4102
        assert handle.container_port == 4101
        assert ds.get_dev_server(_THREAD, "app") is handle

    def test_is_idempotent(self) -> None:
        first = ds.adopt_handle(_THREAD, "app", 4101, "h", 4102)
        second = ds.adopt_handle(_THREAD, "app", 4101, "h", 4102)
        assert second is first

    def test_replaces_a_dead_handle(self) -> None:
        dead = ds.adopt_handle(_THREAD, "app", 4100, "h", 1)
        dead.status = "error"
        fresh = ds.adopt_handle(_THREAD, "app", 4101, "h", 4102)
        assert fresh is not dead
        assert fresh.status == "ready" and fresh.port == 4102


class TestHasLiveDevServer:
    def test_no_handles_is_false(self) -> None:
        assert ds.has_live_dev_server(_THREAD) is False

    def test_starting_handle_is_live(self) -> None:
        h = ds.adopt_handle(_THREAD, "app", 4100, "127.0.0.1", 4101)
        h.status = "starting"
        assert ds.has_live_dev_server(_THREAD) is True

    def test_ready_handle_with_listening_port_is_live(self) -> None:
        listener = _Listener()
        try:
            ds.adopt_handle(_THREAD, "app", 4100, "127.0.0.1", listener.port)
            assert ds.has_live_dev_server(_THREAD) is True
        finally:
            listener.close()

    def test_ready_handle_with_dead_port_is_not_live(self) -> None:
        with socket.socket() as s:
            s.bind(("127.0.0.1", 0))
            port = s.getsockname()[1]
        ds.adopt_handle(_THREAD, "app", 4100, "127.0.0.1", port)
        assert ds.has_live_dev_server(_THREAD) is False

    def test_error_handle_is_not_live(self) -> None:
        h = ds.adopt_handle(_THREAD, "app", 4100, "127.0.0.1", 4101)
        h.status = "error"
        assert ds.has_live_dev_server(_THREAD) is False
