"""Regression tests for the ``/dev-status`` absproxy fallback field.

Pinned by the 2026-08-14 incident where the panel's Browser tab was waiting on
a stale ``/api/sandbox/preview/{tid}`` URL that nothing was listening on,
while the actual site was reachable via ``/api/sandbox/absproxy/{tid}/{port}/``.
The status response now carries both ``url`` (canonical preview) and
``absproxy_url`` (generic proxy) so the Browser tab can fall back without
guessing the host:port.
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
    listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    listener.bind((host, 0))
    port = listener.getsockname()[1]
    listener.listen()
    try:
        yield host, port
    finally:
        listener.close()


def _make_client(monkeypatch):
    from fastapi import FastAPI

    from app.gateway.routers.sandbox import router as sandbox_router

    app = FastAPI()
    app.include_router(sandbox_router)

    from app.gateway.routers import sandbox as sandbox_module

    def _always_owned(thread_id: str) -> bool:
        return thread_id == "t-status"

    # monkeypatch, not a raw assignment: a bare rebind here never unwinds, and
    # because this stub is *narrowing* (False for every other thread id) it made
    # unrelated ownership checks fail for the rest of the session.
    monkeypatch.setattr(sandbox_module, "_caller_owns_thread", _always_owned)

    from fastapi.testclient import TestClient

    return TestClient(app)


def test_dev_status_includes_host_and_absproxy_when_running(monkeypatch):
    import deerflow.sandbox.dev_server as ds

    client = _make_client(monkeypatch)
    with _temp_listener() as (host, port):
        # Register the listener as a "ready" dev server.
        ds.register_external_dev_server("t-status", port, host=host)
        try:
            resp = client.get("/api/sandbox/dev-status", params={"thread_id": "t-status"})
            assert resp.status_code == 200, resp.text
            body = resp.json()
            assert body["running"] is True
            assert body["status"] == "ready"
            assert body["host"] == host
            assert body["port"] == port
            # The two URL shapes the Browser tab needs to choose between.
            assert body["url"].endswith("/api/sandbox/preview/t-status/")
            assert body["absproxy_url"].endswith(f"/api/sandbox/absproxy/t-status/{port}/")
        finally:
            ds._servers.pop(ds._server_key("t-status", ds.DEFAULT_LABEL), None)


def test_dev_status_returns_nulls_when_no_server_running(monkeypatch):
    import deerflow.sandbox.dev_server as ds

    client = _make_client(monkeypatch)
    resp = client.get("/api/sandbox/dev-status", params={"thread_id": "t-status"})
    assert resp.status_code == 200
    body = resp.json()
    assert body["running"] is False
    assert body["status"] == "stopped"
    assert body["port"] is None
    assert body["url"] is None
    assert body["absproxy_url"] is None
