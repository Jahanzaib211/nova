"""Tests for the self-service credit-request loop: user submits, operator
lists + resolves (approve grants credits, decline doesn't)."""

import asyncio
import os

import pytest
from fastapi.testclient import TestClient

os.environ.setdefault("AUTH_JWT_SECRET", "test-secret-key-credit-req-min-32-chars!")

from app.gateway.auth.config import AuthConfig, set_auth_config

_TEST_SECRET = "test-secret-key-credit-req-min-32-chars!"
_PASSWORD = "Tr0ub4dor3a"


@pytest.fixture()
def app(tmp_path):
    from app.gateway import deps
    from app.gateway.app import create_app
    from deerflow.persistence.engine import close_engine, init_engine

    set_auth_config(AuthConfig(jwt_secret=_TEST_SECRET))
    url = f"sqlite+aiosqlite:///{tmp_path}/credit_req.db"
    asyncio.run(init_engine("sqlite", url=url, sqlite_dir=str(tmp_path)))
    deps._cached_local_provider = None
    deps._cached_repo = None
    try:
        yield create_app()
    finally:
        deps._cached_local_provider = None
        deps._cached_repo = None
        asyncio.run(close_engine())


def _csrf(c: TestClient) -> dict[str, str]:
    t = c.cookies.get("csrf_token")
    return {"X-CSRF-Token": t} if t else {}


def _user_client(app, email: str) -> tuple[TestClient, str]:
    c = TestClient(app)
    assert c.post("/api/v1/auth/register", json={"email": email, "password": _PASSWORD}).status_code == 201
    return c, c.get("/api/v1/auth/me").json()["id"]


def _admin_client(app) -> TestClient:
    c = TestClient(app)
    assert c.post("/api/v1/auth/initialize", json={"email": "admin@x.com", "password": _PASSWORD}).status_code == 201
    return c


def test_user_can_submit_request_and_it_shows_pending(app):
    c, _ = _user_client(app, "u@x.com")
    resp = c.post("/api/v1/credits/request", json={"reason": "need more", "requested_tokens": 500000}, headers=_csrf(c))
    assert resp.status_code == 200, resp.text
    assert resp.json()["status"] == "pending"
    # credits endpoint reflects the pending status.
    assert c.get("/api/v1/credits").json()["request_status"] == "pending"


def test_resubmit_updates_same_pending(app):
    c, _ = _user_client(app, "u@x.com")
    c.post("/api/v1/credits/request", json={"reason": "first"}, headers=_csrf(c))
    c.post("/api/v1/credits/request", json={"reason": "second"}, headers=_csrf(c))
    admin = _admin_client(app)
    reqs = admin.get("/api/v1/admin/credit-requests").json()
    assert reqs["pending"] == 1  # not two


def test_operator_lists_and_approves_grants_credits(app):
    c, uid = _user_client(app, "member@x.com")
    c.post("/api/v1/credits/request", json={"reason": "hit the wall"}, headers=_csrf(c))

    admin = _admin_client(app)
    inbox = admin.get("/api/v1/admin/credit-requests").json()
    assert inbox["pending"] == 1
    req_id = inbox["data"][0]["id"]
    assert inbox["data"][0]["email"] == "member@x.com"

    resolve = admin.request(
        "POST",
        f"/api/v1/admin/credit-requests/{req_id}/resolve",
        json={"approve": True, "daily_bonus_tokens": 500000, "days": 7},
        headers=_csrf(admin),
    )
    assert resolve.status_code == 200, resolve.text

    # The user now has the bonus, and their request shows approved.
    detail = admin.get(f"/api/v1/admin/users/{uid}").json()
    assert detail["bonus_daily_tokens"] == 500000
    assert c.get("/api/v1/credits").json()["request_status"] == "approved"

    # It's out of the pending inbox + audited.
    assert admin.get("/api/v1/admin/credit-requests").json()["pending"] == 0
    assert any(r["action"] == "resolve-credit-request" for r in admin.get("/api/v1/admin/audit").json()["data"])


def test_decline_does_not_grant(app):
    c, uid = _user_client(app, "nope@x.com")
    c.post("/api/v1/credits/request", json={"reason": "please"}, headers=_csrf(c))
    admin = _admin_client(app)
    req_id = admin.get("/api/v1/admin/credit-requests").json()["data"][0]["id"]
    admin.request("POST", f"/api/v1/admin/credit-requests/{req_id}/resolve", json={"approve": False}, headers=_csrf(admin))
    assert admin.get(f"/api/v1/admin/users/{uid}").json()["bonus_daily_tokens"] == 0
    assert c.get("/api/v1/credits").json()["request_status"] == "declined"


def test_resolve_requires_admin(app):
    c, _ = _user_client(app, "u@x.com")
    c.post("/api/v1/credits/request", json={"reason": "x"}, headers=_csrf(c))
    admin = _admin_client(app)
    req_id = admin.get("/api/v1/admin/credit-requests").json()["data"][0]["id"]
    # A regular user cannot resolve.
    attacker, _ = _user_client(app, "attacker@x.com")
    r = attacker.request("POST", f"/api/v1/admin/credit-requests/{req_id}/resolve", json={"approve": True}, headers=_csrf(attacker))
    assert r.status_code == 403
