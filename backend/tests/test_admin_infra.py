"""Tests for the admin-only /api/v1/admin/infra/* endpoints.

The five endpoints are read-only proxies to the sandbox provisioner's
/api/infra/* surface. They do not mutate the cluster. Only the pod
logs endpoint is audit-logged (logs can contain secrets), but the
list endpoints are audited too — the audit trail records every
operator who *can see* what.

The tests stub the provisioner URL so requests never leave the
test process. The gateway's respx-free approach is to monkeypatch
``_provisioner_url`` at module level and ``infra_client._get`` via
``unittest.mock.AsyncMock`` so the asyncio interaction is hermetic.
"""

from __future__ import annotations

import os
from unittest.mock import AsyncMock, patch

import pytest
from fastapi.testclient import TestClient

os.environ.setdefault("AUTH_JWT_SECRET", "test-secret-key-admin-infra-32-chars")

from app.gateway.auth.config import AuthConfig, set_auth_config

_TEST_SECRET = "test-secret-key-admin-infra-32-chars"
_PASSWORD = "Tr0ub4dor3a"


@pytest.fixture()
def app(tmp_path):
    from app.gateway import deps
    from app.gateway.app import create_app
    from deerflow.persistence.engine import close_engine, init_engine

    set_auth_config(AuthConfig(jwt_secret=_TEST_SECRET))
    url = f"sqlite+aiosqlite:///{tmp_path}/admin_infra.db"
    asyncio_run = __import__("asyncio").run
    asyncio_run(init_engine("sqlite", url=url, sqlite_dir=str(tmp_path)))
    deps._cached_local_provider = None
    deps._cached_repo = None
    try:
        yield create_app()
    finally:
        deps._cached_local_provider = None
        deps._cached_repo = None
        asyncio_run(close_engine())


def _csrf(client: TestClient) -> dict[str, str]:
    t = client.cookies.get("csrf_token")
    return {"X-CSRF-Token": t} if t else {}


def _admin_client(app) -> TestClient:
    c = TestClient(app)
    resp = c.post("/api/v1/auth/initialize", json={"email": "admin@x.com", "password": _PASSWORD})
    assert resp.status_code == 201, resp.text
    return c


def _regular_user(app, email="someone@x.com") -> TestClient:
    c = TestClient(app)
    resp = c.post("/api/v1/auth/register", json={"email": email, "password": _PASSWORD})
    assert resp.status_code == 201, resp.text
    return c


def _patched_provisioner_url(url: str = "http://provisioner.invalid"):
    """Monkeypatch the provisioner URL resolver + the per-endpoint HTTP calls."""
    from app.gateway.routers import admin_infra

    return patch.object(admin_infra, "_provisioner_url", lambda: url)


# ── /pods ────────────────────────────────────────────────────────────────


def test_admin_infra_pods_returns_provisioner_payload_and_audits(app):
    from app.gateway import infra_client as ic

    payload = {"items": [{"name": "gw-1", "phase": "Running"}], "total": 1}
    admin = _admin_client(app)
    with _patched_provisioner_url(), patch.object(ic, "list_pods", AsyncMock(return_value=payload)):
        resp = admin.get("/api/v1/admin/infra/pods")
    assert resp.status_code == 200
    assert resp.json() == payload

    audit = admin.get("/api/v1/admin/audit").json()["data"]
    actions = [row["action"] for row in audit]
    assert "view-infra-pods" in actions


def test_admin_infra_pods_403_for_regular_user(app):
    user = _regular_user(app)
    resp = user.get("/api/v1/admin/infra/pods")
    assert resp.status_code == 403


def test_admin_infra_pods_503_when_provisioner_unconfigured(app):
    """If sandbox.provisioner_url is unset, the router returns 503
    with a descriptive message — not a 500."""
    from fastapi import HTTPException

    from app.gateway.routers import admin_infra

    admin = _admin_client(app)

    def _raise_503():
        raise HTTPException(status_code=503, detail="No sandbox provisioner is configured (sandbox.provisioner_url).")

    with patch.object(admin_infra, "_provisioner_url", _raise_503):
        resp = admin.get("/api/v1/admin/infra/pods")
    assert resp.status_code == 503
    assert "provisioner" in resp.json()["detail"].lower()


def test_admin_infra_pods_returns_502_when_provisioner_unreachable(app):
    """InfraClientError.status_code is propagated to the HTTP response."""
    from app.gateway import infra_client as ic
    from app.gateway.routers import admin_infra

    admin = _admin_client(app)
    with _patched_provisioner_url(), patch.object(ic, "list_pods", AsyncMock(side_effect=ic.InfraClientError(502, "Provisioner unreachable: connection refused"))):
        resp = admin.get("/api/v1/admin/infra/pods")
    assert resp.status_code == 502
    assert "unreachable" in resp.json()["detail"].lower()


# ── /deployments ────────────────────────────────────────────────────────


def test_admin_infra_deployments_returns_payload_and_audits(app):
    from app.gateway import infra_client as ic

    payload = {"items": [{"name": "gateway", "replicas": 2}]}
    admin = _admin_client(app)
    with _patched_provisioner_url(), patch.object(ic, "list_deployments", AsyncMock(return_value=payload)):
        resp = admin.get("/api/v1/admin/infra/deployments")
    assert resp.status_code == 200
    assert resp.json() == payload

    audit = admin.get("/api/v1/admin/audit").json()["data"]
    actions = [row["action"] for row in audit]
    assert "view-infra-deployments" in actions


def test_admin_infra_deployments_403_for_regular_user(app):
    user = _regular_user(app)
    resp = user.get("/api/v1/admin/infra/deployments")
    assert resp.status_code == 403


# ── /events ────────────────────────────────────────────────────────────


def test_admin_infra_events_returns_payload_audits_and_passes_limit(app):
    from app.gateway import infra_client as ic

    payload = {"items": [{"type": "Normal", "reason": "Started"}]}
    admin = _admin_client(app)
    with _patched_provisioner_url(), patch.object(ic, "list_events", AsyncMock(return_value=payload)) as mock_events:
        resp = admin.get("/api/v1/admin/infra/events", params={"limit": 25})
    assert resp.status_code == 200
    assert resp.json() == payload
    # The route forwards the limit to the provisioner.
    mock_events.assert_awaited_once()
    args, kwargs = mock_events.call_args
    assert kwargs.get("limit") == 25 or (len(args) > 1 and args[1] == 25)

    audit = admin.get("/api/v1/admin/audit").json()["data"]
    actions = [row["action"] for row in audit]
    assert "view-infra-events" in actions


def test_admin_infra_events_403_for_regular_user(app):
    user = _regular_user(app)
    resp = user.get("/api/v1/admin/infra/events")
    assert resp.status_code == 403


def test_admin_infra_events_rejects_out_of_range_limit(app):
    """The Query constraints (1..500) surface as 422, not a 500."""
    admin = _admin_client(app)
    assert admin.get("/api/v1/admin/infra/events", params={"limit": 0}).status_code == 422
    assert admin.get("/api/v1/admin/infra/events", params={"limit": 501}).status_code == 422


# ── /metrics ────────────────────────────────────────────────────────────


def test_admin_infra_metrics_returns_payload_and_audits(app):
    from app.gateway import infra_client as ic

    payload = {"nodes": [{"name": "node-1", "cpu": 0.4, "memory": 0.6}]}
    admin = _admin_client(app)
    with _patched_provisioner_url(), patch.object(ic, "get_metrics", AsyncMock(return_value=payload)):
        resp = admin.get("/api/v1/admin/infra/metrics")
    assert resp.status_code == 200
    assert resp.json() == payload

    audit = admin.get("/api/v1/admin/audit").json()["data"]
    assert any(row["action"] == "view-infra-metrics" for row in audit)


def test_admin_infra_metrics_403_for_regular_user(app):
    user = _regular_user(app)
    resp = user.get("/api/v1/admin/infra/metrics")
    assert resp.status_code == 403


# ── /pods/{pod_name}/logs ────────────────────────────────────────────────


def test_admin_infra_pod_logs_returns_payload_and_audits(app):
    from app.gateway import infra_client as ic

    payload = {"pod": "gateway-1", "container": "gateway", "lines": ["line 1", "line 2"]}
    admin = _admin_client(app)
    with _patched_provisioner_url(), patch.object(ic, "get_pod_logs", AsyncMock(return_value=payload)) as mock_logs:
        resp = admin.get("/api/v1/admin/infra/pods/gateway-1/logs", params={"tail": 50})
    assert resp.status_code == 200
    assert resp.json() == payload
    # The route forwards pod name + tail to the provisioner.
    mock_logs.assert_awaited_once()
    args, kwargs = mock_logs.call_args
    # Signature: get_pod_logs(provisioner_url, pod_name, tail, container).
    # The provisioner URL is the host only; the path is f-string'd inside
    # the function. The second positional is the pod name.
    assert args[0] == "http://provisioner.invalid", args[0]
    assert args[1] == "gateway-1", args[1]
    assert kwargs.get("tail") == 50, kwargs

    audit = admin.get("/api/v1/admin/audit").json()["data"]
    matching = [r for r in audit if r["action"] == "view-pod-logs"]
    assert matching
    assert matching[0]["payload"]["pod"] == "gateway-1"
    assert matching[0]["payload"]["tail"] == 50


def test_admin_infra_pod_logs_passes_container_param(app):
    from app.gateway import infra_client as ic

    admin = _admin_client(app)
    with _patched_provisioner_url(), patch.object(ic, "get_pod_logs", AsyncMock(return_value={"pod": "gw", "container": "sidecar", "lines": []})) as mock_logs:
        resp = admin.get(
            "/api/v1/admin/infra/pods/gw/logs",
            params={"container": "sidecar", "tail": 10},
        )
    assert resp.status_code == 200
    mock_logs.assert_awaited_once()
    args, kwargs = mock_logs.call_args
    assert kwargs.get("container") == "sidecar" or (len(args) > 2 and args[2] == "sidecar")


def test_admin_infra_pod_logs_403_for_regular_user(app):
    user = _regular_user(app)
    resp = user.get("/api/v1/admin/infra/pods/gateway-1/logs")
    assert resp.status_code == 403


def test_admin_infra_pod_logs_rejects_out_of_range_tail(app):
    """1..2000 enforced by the Query constraint."""
    admin = _admin_client(app)
    assert admin.get("/api/v1/admin/infra/pods/gateway-1/logs", params={"tail": 0}).status_code == 422
    assert admin.get("/api/v1/admin/infra/pods/gateway-1/logs", params={"tail": 5000}).status_code == 422


def test_admin_infra_pod_logs_returns_502_on_provisioner_error(app):
    """The InfraClientError propagates as the original HTTP status."""
    from app.gateway import infra_client as ic

    admin = _admin_client(app)
    with _patched_provisioner_url(), patch.object(ic, "get_pod_logs", AsyncMock(side_effect=ic.InfraClientError(503, "Provisioner returned 503: maintenance"))):
        resp = admin.get("/api/v1/admin/infra/pods/gateway-1/logs")
    assert resp.status_code == 503
    assert "maintenance" in resp.json()["detail"].lower()


# ── ops-console token path ──────────────────────────────────────────────


def test_admin_infra_endpoints_accept_ops_console_token(app):
    """The ops-console service token authenticates as a synthetic admin.
    Mirrors the same shape as the X-Nova-Ops-Token admin reachability
    for the users endpoints — every surface that the god-mode UI needs
    is reachable through the BFF."""
    from app.gateway import infra_client as ic

    monkey = pytest.MonkeyPatch()
    monkey.setenv("NOVA_OPS_TOKEN", "ops-token-test")
    try:
        with _patched_provisioner_url(), patch.object(ic, "list_pods", AsyncMock(return_value={"items": []})):
            client = TestClient(app)
            resp = client.get(
                "/api/v1/admin/infra/pods",
                headers={"X-Nova-Ops-Token": "ops-token-test"},
            )
    finally:
        monkey.undo()
    assert resp.status_code == 200

    # The audit row should be stamped with the synthetic admin actor.
    admin = _admin_client(app)
    audit = admin.get("/api/v1/admin/audit", params={"target_user_id": "null_is_omitted"}).json()
    # Easier: just find the view-infra-pods row and assert actor.
    audit = admin.get("/api/v1/admin/audit").json()["data"]
    matching = [r for r in audit if r["action"] == "view-infra-pods"]
    assert matching
    assert matching[0]["actor"] == "ops-console"


def test_admin_infra_returns_501_when_no_provisioner_configured(app):
    """Unconfigured must be distinguishable from unreachable.

    A provisioner is optional. Most deployments run sandboxes locally and never
    set ``sandbox.provisioner_url``; for them the infra endpoints describe a
    feature that does not exist, which is 501. A *configured* provisioner that
    is down returns 503 (see ``..._returns_502_on_provisioner_error``), and the
    Ops console renders the two differently — a neutral note versus a red alarm.
    """
    from types import SimpleNamespace

    from app.gateway.routers import admin_infra

    admin = _admin_client(app)
    stub = SimpleNamespace(sandbox=SimpleNamespace(provisioner_url=None))

    with patch.object(admin_infra, "get_app_config", lambda: stub):
        resp = admin.get("/api/v1/admin/infra/pods")

    assert resp.status_code == 501
    assert "provisioner_url" in resp.json()["detail"]
