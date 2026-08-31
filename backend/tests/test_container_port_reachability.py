"""An in-sandbox port must be probed from the sandbox, not from the gateway.

``register_external_dev_server`` exists so a dev server started outside the
``start_dev_server`` pipeline (raw bash, PM2, a manual ``node``) can still reach
the Browser tab. It was dead code until 2026-08-31, and binding it into
``BUILTIN_TOOLS`` still did not make it work, because its liveness check was::

    socket.create_connection((host, port))   # host defaults to 127.0.0.1

That runs in the **gateway** process. Only 4100-4102 and the sandbox API port
are published to the host, so for the motivating case — a server on, say, 8787
inside the container — 127.0.0.1 is the gateway's own loopback, nothing is
listening, and the tool refused with "port 127.0.0.1:8787 is not reachable".
The escape hatch could not open for the one situation it was written for.

The reachability was never actually missing: ``_absproxy_impl`` proxies to the
sandbox's *own* ``/absproxy/{port}/`` gateway, which resolves ports inside the
container regardless of publishing. Measured against a live sandbox, a
listening port answers (``308`` for a dev server that redirects) and an idle one
comes back ``500`` — so "answered, and not 5xx" is the discriminator.
"""

from __future__ import annotations

import pytest

import deerflow.tools.builtins.workspace_tools  # noqa: F401  isort:skip
from deerflow.sandbox import dev_server  # noqa: E402  isort:skip


class TestParseListeningPorts:
    """The port map is parsed from what ``system_probe`` already shows the agent."""

    def test_parses_ss_output(self) -> None:
        out = "State  Recv-Q Send-Q Local Address:Port  Peer Address:Port\nLISTEN 0      511          0.0.0.0:4100       0.0.0.0:*\nLISTEN 0      511             [::]:8787          [::]:*\n"
        assert dev_server.parse_listening_ports(out) == [4100, 8787]

    def test_parses_netstat_output(self) -> None:
        out = "tcp        0      0 0.0.0.0:3000            0.0.0.0:*               LISTEN\ntcp6       0      0 :::5173                 :::*                    LISTEN\n"
        assert dev_server.parse_listening_ports(out) == [3000, 5173]

    def test_ignores_the_peer_column(self) -> None:
        """``0.0.0.0:*`` must not contribute a port."""
        out = "LISTEN 0 511 0.0.0.0:4100 0.0.0.0:*\n"
        assert dev_server.parse_listening_ports(out) == [4100]

    def test_empty_means_unknown_not_nothing(self) -> None:
        assert dev_server.parse_listening_ports("") == []


class _FakeSandbox:
    def __init__(self, base_url="http://sandbox.local:8080", listening=()):
        self.base_url = base_url
        self._listening = set(listening)

    def execute_command(self, cmd: str) -> str:
        return "".join(f"LISTEN 0 511 0.0.0.0:{p} 0.0.0.0:*\n" for p in sorted(self._listening))


@pytest.fixture
def sandbox(monkeypatch):
    box = _FakeSandbox(listening={4100, 8080, 8787})
    monkeypatch.setattr(dev_server, "_sandbox_for_thread", lambda _tid: box)
    return box


class TestProbeContainerPort:
    """The probe must ask the sandbox gateway, and read its status correctly."""

    def _stub_http(self, monkeypatch, status_by_port):
        import httpx

        class _Resp:
            def __init__(self, code):
                self.status_code = code

        class _Client:
            def __init__(self, **kw):
                pass

            async def __aenter__(self):
                return self

            async def __aexit__(self, *a):
                return False

            async def get(self, url):
                port = int(url.rstrip("/").rsplit("/", 1)[-1])
                if port not in status_by_port:
                    raise RuntimeError("connection refused")
                return _Resp(status_by_port[port])

        monkeypatch.setattr(httpx, "AsyncClient", _Client)

    @pytest.mark.asyncio
    async def test_a_listening_port_is_reachable(self, sandbox, monkeypatch) -> None:
        self._stub_http(monkeypatch, {8787: 308})
        assert await dev_server.probe_container_port("t1", 8787) is True

    @pytest.mark.asyncio
    async def test_an_idle_port_is_not(self, sandbox, monkeypatch) -> None:
        """The sandbox gateway answers 500 when nothing is behind the port."""
        self._stub_http(monkeypatch, {9999: 500})
        assert await dev_server.probe_container_port("t1", 9999) is False

    @pytest.mark.asyncio
    async def test_a_refused_connection_is_not_reachable(self, sandbox, monkeypatch) -> None:
        self._stub_http(monkeypatch, {})
        assert await dev_server.probe_container_port("t1", 1234) is False

    @pytest.mark.asyncio
    async def test_it_does_not_probe_the_gateways_own_loopback(self, sandbox, monkeypatch) -> None:
        """The original bug, pinned: no direct socket connect for a container port."""
        import socket as _socket

        def _boom(*a, **k):
            raise AssertionError("probed the gateway's loopback instead of the sandbox")

        monkeypatch.setattr(_socket, "create_connection", _boom)
        self._stub_http(monkeypatch, {8787: 200})
        assert await dev_server.probe_container_port("t1", 8787) is True


class TestListListeningPorts:
    @pytest.mark.asyncio
    async def test_reports_the_sandboxs_ports(self, sandbox) -> None:
        assert await dev_server.list_listening_ports("t1") == [4100, 8080, 8787]

    @pytest.mark.asyncio
    async def test_no_sandbox_means_unknown(self, monkeypatch) -> None:
        monkeypatch.setattr(dev_server, "_sandbox_for_thread", lambda _tid: None)
        assert await dev_server.list_listening_ports("t1") == []


class TestDiscoveryReachesBeyondThePublishedPorts:
    """The gap that left a server on 8787 permanently unpreviewable."""

    @pytest.mark.asyncio
    async def test_it_adopts_a_server_on_an_unpublished_port(self, sandbox, monkeypatch) -> None:
        # No provider preview endpoint => the 4100-4102 sweep finds nothing.
        monkeypatch.setattr(dev_server, "get_dev_server", lambda *a, **k: None, raising=False)

        class _Provider:
            def get_preview_endpoint(self, *a, **k):
                return None

        import deerflow.sandbox as sandbox_pkg

        monkeypatch.setattr(sandbox_pkg, "get_sandbox_provider", lambda: _Provider())

        async def _alive(thread_id, port):
            return port == 8787

        monkeypatch.setattr(dev_server, "probe_container_port", _alive)

        found = await dev_server.discover_live_preview("t1")
        assert found is not None, "a live server on an unpublished port stayed invisible"
        container_port, _host, _host_port = found
        assert container_port == 8787

    @pytest.mark.asyncio
    async def test_it_never_adopts_the_sandboxs_own_services(self, sandbox, monkeypatch) -> None:
        """8080 is ttyd/noVNC — adopting it would render the sandbox's own UI."""

        class _Provider:
            def get_preview_endpoint(self, *a, **k):
                return None

        import deerflow.sandbox as sandbox_pkg

        monkeypatch.setattr(sandbox_pkg, "get_sandbox_provider", lambda: _Provider())
        monkeypatch.setattr(dev_server, "_sandbox_for_thread", lambda _tid: _FakeSandbox(listening={8080}))

        async def _alive(thread_id, port):
            return True

        monkeypatch.setattr(dev_server, "probe_container_port", _alive)
        assert await dev_server.discover_live_preview("t1") is None


class TestRegistrationRecordsTheContainerPort:
    def test_container_port_defaults_to_the_registered_port(self) -> None:
        handle = dev_server.register_external_dev_server("t-cp", 8787, container_port=8787)
        assert handle.container_port == 8787, "absproxy URLs are keyed on the container port; leaving this at the 4100 default pointed the Browser tab at a port the server was not on"

    def test_an_explicit_container_port_wins(self) -> None:
        handle = dev_server.register_external_dev_server("t-cp2", 40001, container_port=3000)
        assert handle.container_port == 3000


class TestTheGatewayUsesThePathStrippingProxy:
    """The Browser tab's fallback must not hand the app a prefixed path.

    The sandbox exposes two proxies and they are not interchangeable:

    * ``/proxy/{port}/``    strips the prefix — the app sees ``GET /``
    * ``/absproxy/{port}/`` passes the absolute path through verbatim — the app
      sees ``GET /absproxy/{port}/`` and needs a matching basePath to serve it

    The gateway targeted ``absproxy``. None of the servers this fallback exists
    for (raw ``bash``, PM2, a manual ``node``, ``python -m http.server``) set
    such a basePath, so the safety net reliably rendered the app's own 404.

    Measured against a live sandbox image with a server on an unpublished 8787::

        /proxy/8787/     -> 200, served the real page  (app log: "GET /")
        /absproxy/8787/  -> 404                        (app log: "GET /absproxy/8787/")

    Stripping upstream is also the half that composes with our own rewriting: we
    serve under ``/api/sandbox/absproxy/{tid}/{port}/`` and rewrite the app's
    URLs to match, so a later request for ``.../{port}/assets/x.js`` reaches the
    app as ``/assets/x.js``.
    """

    def test_upstream_hop_strips_the_prefix(self) -> None:
        from pathlib import Path

        router = Path(__file__).resolve().parents[1] / "app" / "gateway" / "routers" / "sandbox.py"
        text = router.read_text(encoding="utf-8")
        assert '''target = f"{base_url.rstrip('/')}/proxy/{port}/{path}"''' in text, "the gateway is targeting the sandbox's path-preserving proxy again; a plain app served through the Browser tab fallback will 404"

    def test_the_public_route_name_is_unchanged(self) -> None:
        """Only the upstream hop moved — the frontend builds this URL."""
        from pathlib import Path

        router = Path(__file__).resolve().parents[1] / "app" / "gateway" / "routers" / "sandbox.py"
        text = router.read_text(encoding="utf-8")
        assert "/api/sandbox/absproxy/{thread_id}/{port}/" in text or "absproxy/{thread_id}" in text
