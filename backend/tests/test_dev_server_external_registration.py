"""Regression tests for the externally-started dev-server registration path.

Pinned by the 2026-08-14 incident where the agent's real Node server was
listening on port 3000 inside the sandbox but the runtime's ``start_dev_server``
calls silently failed to bind, leaving the panel spinning on a stale port
while the actual site was reachable via ``/api/sandbox/absproxy/...``. The
fix adds ``POST /api/sandbox/dev-external`` so a dev server started outside
``start_dev_server`` can be registered and surfaced through the same status
poll as a runtime-spawned one.
"""

from __future__ import annotations

import socket
import sys
from contextlib import contextmanager
from pathlib import Path

import pytest

_HERE = Path(__file__).resolve().parent
_BACKEND = _HERE.parent
if str(_BACKEND) not in sys.path:
    sys.path.insert(0, str(_BACKEND))


@contextmanager
def _temp_listener(host: str = "127.0.0.1"):
    """Open a real TCP listener on an ephemeral port for the duration of the test."""
    listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    listener.bind((host, 0))
    port = listener.getsockname()[1]
    listener.listen()
    try:
        yield host, port
    finally:
        listener.close()


# ─────────────────────────────────────────────────────────────────────────────
# Module-level contract: register_external_dev_server
# ─────────────────────────────────────────────────────────────────────────────


def test_register_external_dev_server_creates_ready_handle():
    import deerflow.sandbox.dev_server as ds

    with _temp_listener() as (host, port):
        handle = ds.register_external_dev_server("t-ext", port, host=host)
    try:
        assert handle.status == "ready"
        assert handle.port == port
        assert handle.host == host
        assert handle.thread_id == "t-ext"
        # The lookup path must find the new handle.
        assert ds.get_dev_server("t-ext").status == "ready"
    finally:
        ds._servers.pop(ds._server_key("t-ext", ds.DEFAULT_LABEL), None)


def test_register_external_dev_server_is_idempotent():
    import deerflow.sandbox.dev_server as ds

    with _temp_listener() as (host, port):
        first = ds.register_external_dev_server("t-idem", port, host=host)
        second = ds.register_external_dev_server("t-idem", port, host=host)
    try:
        assert first.status == "ready"
        assert second.status == "ready"
        assert first.thread_id == second.thread_id
    finally:
        ds._servers.pop(ds._server_key("t-idem", ds.DEFAULT_LABEL), None)


# ─────────────────────────────────────────────────────────────────────────────
# HTTP endpoint: POST /api/sandbox/dev-external
# ─────────────────────────────────────────────────────────────────────────────


def _make_client():
    """Build a starlette TestClient that doesn't require a running DB.

    The router's other endpoints (logs, todo, status) reach the gateway's
    broader lifespan machinery; for these tests we mount only the sandbox
    router onto a minimal FastAPI app.
    """
    from fastapi import FastAPI

    from app.gateway.routers.sandbox import router as sandbox_router

    app = FastAPI()
    app.include_router(sandbox_router)

    # The dev-external endpoint calls ``_caller_owns_thread`` which resolves a
    # thread directory under the effective user. In a hermetic test there is
    # no per-user bucket, so patch the helper to return True for the IDs we
    # use.
    from app.gateway.routers import sandbox as sandbox_module

    def _always_owned(thread_id: str) -> bool:
        return thread_id in {"t-http-ok", "t-http-bad"}

    sandbox_module._caller_owns_thread = _always_owned

    from fastapi.testclient import TestClient

    return TestClient(app)


def test_dev_external_endpoint_registers_and_returns_handles():
    import deerflow.sandbox.dev_server as ds

    client = _make_client()
    with _temp_listener() as (host, port):
        resp = client.post(
            "/api/sandbox/dev-external",
            params={"thread_id": "t-http-ok", "port": port, "host": host},
        )
    try:
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["status"] == "ready"
        assert body["port"] == port
        assert body["host"] == host
        assert body["absproxy_url"].endswith(f"/api/sandbox/absproxy/t-http-ok/{port}/")
    finally:
        ds._servers.pop(ds._server_key("t-http-ok", ds.DEFAULT_LABEL), None)


def test_dev_external_endpoint_refuses_unreachable_port():
    import deerflow.sandbox.dev_server as ds
    from app.gateway.routers import sandbox as sandbox_module

    client = _make_client()
    # Find a port nothing is bound to right now.
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        free_port = s.getsockname()[1]
    try:
        resp = client.post(
            "/api/sandbox/dev-external",
            params={"thread_id": "t-http-bad", "port": free_port, "host": "127.0.0.1"},
        )
        assert resp.status_code == 400, resp.text
        assert "not reachable" in resp.json()["detail"]
    finally:
        # Nothing to clean up — registration must have been refused.
        assert ds.get_dev_server("t-http-bad") is None


class TestExternalRegistrationToolIsReachable:
    """The escape hatch is only an escape hatch if the agent can call it.

    `register_external_dev_server_tool` was fully implemented (port validation,
    a liveness probe that refuses to advertise a phantom server) and documented
    in backend/CLAUDE.md as "the agent-facing tool" -- but it was never imported
    into `deerflow.tools.tools` and never added to `BUILTIN_TOOLS`, so it was
    unreachable dead code.

    The cost is not theoretical. A dev server started outside the pipeline (raw
    bash, PM2, a manual `node`) can only reach the Browser tab two ways:
    `discover_live_preview`, which probes only `_PREVIEW_CONTAINER_PORTS`
    (4100-4102), or this tool. With the tool unbound, anything on another port
    was unpreviewable and the panel sat on "Start Live Preview" forever, with
    the agent left to conclude the tool "isn't in this thread's whitelist".
    """

    def test_tool_is_in_the_builtin_registry(self):
        from deerflow.tools.tools import BUILTIN_TOOLS

        names = {getattr(t, "name", None) for t in BUILTIN_TOOLS}
        assert "register_external_dev_server" in names, (
            "register_external_dev_server is implemented but not bound; "
            f"BUILTIN_TOOLS exposes {sorted(n for n in names if n)}"
        )

    def test_the_port_discovery_gap_it_covers_is_real(self):
        """Pin the reason the tool must exist: discovery is a 3-port probe."""
        from deerflow.sandbox.dev_server import _PREVIEW_CONTAINER_PORTS

        assert 8787 not in _PREVIEW_CONTAINER_PORTS
        assert len(_PREVIEW_CONTAINER_PORTS) == 3
