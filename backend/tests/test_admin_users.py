"""Tests for the admin registration-visibility endpoints.

Covers GET /api/v1/admin/users and /api/v1/admin/users/stats: the roster
contents, plan breakdown, growth counts, pagination, and the admin-only
authorization boundary (regular user → 403, no session → 401).
"""

import asyncio
import os

import pytest
from fastapi.testclient import TestClient

os.environ.setdefault("AUTH_JWT_SECRET", "test-secret-key-admin-users-min-32chars")

from app.gateway.auth.config import AuthConfig, set_auth_config

_TEST_SECRET = "test-secret-key-admin-users-min-32chars"
_PASSWORD = "Tr0ub4dor3a"


@pytest.fixture()
def app(tmp_path):
    """Fresh SQLite engine + auth config + a create_app() instance per test."""
    from app.gateway import deps
    from app.gateway.app import create_app
    from deerflow.persistence.engine import close_engine, init_engine

    set_auth_config(AuthConfig(jwt_secret=_TEST_SECRET))
    url = f"sqlite+aiosqlite:///{tmp_path}/admin_users.db"
    asyncio.run(init_engine("sqlite", url=url, sqlite_dir=str(tmp_path)))
    deps._cached_local_provider = None
    deps._cached_repo = None
    try:
        yield create_app()
    finally:
        deps._cached_local_provider = None
        deps._cached_repo = None
        asyncio.run(close_engine())


def _register(app, email: str) -> None:
    """Register a regular user via a throwaway client (isolated cookie jar).

    Using a separate client keeps this user's auto-login session from
    clobbering the admin session held by the caller's client.
    """
    c = TestClient(app)
    resp = c.post("/api/v1/auth/register", json={"email": email, "password": _PASSWORD})
    assert resp.status_code == 201, resp.text


def _admin_client(app) -> TestClient:
    """Initialize the first admin and return a client holding its session."""
    c = TestClient(app)
    resp = c.post("/api/v1/auth/initialize", json={"email": "admin@example.com", "password": _PASSWORD})
    assert resp.status_code == 201, resp.text
    return c


def test_admin_list_users_returns_roster(app):
    _register(app, "u1@example.com")
    _register(app, "u2@example.com")
    admin = _admin_client(app)

    resp = admin.get("/api/v1/admin/users")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["total"] == 3  # 2 users + 1 admin
    emails = {row["email"] for row in body["data"]}
    assert {"u1@example.com", "u2@example.com", "admin@example.com"} <= emails
    # Every roster row defaults to the free plan.
    assert all(row["plan"] == "free" for row in body["data"])
    assert body["has_more"] is False


def test_admin_stats_reports_totals_and_plan_breakdown(app):
    _register(app, "a@example.com")
    _register(app, "b@example.com")
    admin = _admin_client(app)

    resp = admin.get("/api/v1/admin/users/stats")
    assert resp.status_code == 200, resp.text
    stats = resp.json()
    assert stats["total"] == 3
    assert stats["by_plan"] == {"free": 3}
    # All three were created just now.
    assert stats["new_last_7_days"] == 3
    assert stats["new_last_30_days"] == 3


def test_admin_users_pagination(app):
    for i in range(5):
        _register(app, f"p{i}@example.com")
    admin = _admin_client(app)  # 6 total now

    page1 = admin.get("/api/v1/admin/users", params={"limit": 2, "offset": 0}).json()
    assert len(page1["data"]) == 2
    assert page1["total"] == 6
    assert page1["has_more"] is True

    page_last = admin.get("/api/v1/admin/users", params={"limit": 2, "offset": 4}).json()
    assert len(page_last["data"]) == 2
    assert page_last["has_more"] is False


def test_admin_users_forbidden_for_regular_user(app):
    # Regular user's own client (auto-logged-in as a non-admin).
    c = TestClient(app)
    assert c.post("/api/v1/auth/register", json={"email": "reg@example.com", "password": _PASSWORD}).status_code == 201

    resp = c.get("/api/v1/admin/users")
    assert resp.status_code == 403


def test_admin_users_requires_auth(app):
    resp = TestClient(app).get("/api/v1/admin/users")
    assert resp.status_code == 401


# ── God-mode controls + ops-token auth ───────────────────────────────────


def _csrf(client: TestClient) -> dict[str, str]:
    token = client.cookies.get("csrf_token")
    return {"X-CSRF-Token": token} if token else {}


def _register_get_id(app, email: str) -> str:
    c = TestClient(app)
    assert c.post("/api/v1/auth/register", json={"email": email, "password": _PASSWORD}).status_code == 201
    return c.get("/api/v1/auth/me").json()["id"]


def _insert_run(app, *, run_id, user_id, tokens, when):
    """Insert a RunRow directly so credit usage is non-zero."""
    from deerflow.persistence.engine import get_session_factory
    from deerflow.persistence.run.model import RunRow

    async def _go():
        async with get_session_factory()() as session:
            session.add(RunRow(run_id=run_id, thread_id="t", user_id=user_id, total_tokens=tokens, created_at=when))
            await session.commit()

    asyncio.run(_go())


def test_ops_token_authenticates_as_admin(app, monkeypatch):
    """A valid X-Nova-Ops-Token reaches admin endpoints without a session."""
    monkeypatch.setenv("NOVA_OPS_TOKEN", "super-secret-ops-token")
    _register(app, "u1@example.com")
    client = TestClient(app)  # no session cookie

    ok = client.get("/api/v1/admin/users", headers={"X-Nova-Ops-Token": "super-secret-ops-token"})
    assert ok.status_code == 200
    assert ok.json()["total"] == 1

    wrong = client.get("/api/v1/admin/users", headers={"X-Nova-Ops-Token": "nope"})
    assert wrong.status_code == 401


def test_ops_token_disabled_when_env_unset(app, monkeypatch):
    monkeypatch.delenv("NOVA_OPS_TOKEN", raising=False)
    resp = TestClient(app).get("/api/v1/admin/users", headers={"X-Nova-Ops-Token": "anything"})
    assert resp.status_code == 401


def test_user_detail_returns_full_standing(app):
    uid = _register_get_id(app, "member@example.com")
    admin = _admin_client(app)
    detail = admin.get(f"/api/v1/admin/users/{uid}").json()
    assert detail["email"] == "member@example.com"
    assert detail["plan"] == "free"
    assert detail["daily_limit"] == 250_000
    assert detail["remaining"] == 250_000
    assert detail["recent_runs"] == []


def test_reset_usage_restores_allowance(app):
    from datetime import UTC, datetime

    uid = _register_get_id(app, "heavy@example.com")
    _insert_run(app, run_id="r1", user_id=uid, tokens=200_000, when=datetime.now(UTC))
    admin = _admin_client(app)

    before = admin.get(f"/api/v1/admin/users/{uid}").json()
    assert before["used"] == 200_000

    resp = admin.request("POST", f"/api/v1/admin/users/{uid}/reset-usage", headers=_csrf(admin))
    assert resp.status_code == 200, resp.text

    after = admin.get(f"/api/v1/admin/users/{uid}").json()
    assert after["used"] == 0
    assert after["remaining"] == 250_000


def test_grant_credits_raises_limit(app):
    uid = _register_get_id(app, "lucky@example.com")
    admin = _admin_client(app)
    resp = admin.request(
        "POST",
        f"/api/v1/admin/users/{uid}/grant",
        json={"daily_bonus_tokens": 500_000, "days": 7},
        headers=_csrf(admin),
    )
    assert resp.status_code == 200, resp.text
    detail = admin.get(f"/api/v1/admin/users/{uid}").json()
    assert detail["bonus_daily_tokens"] == 500_000
    assert detail["daily_limit"] == 750_000


def test_set_and_clear_custom_limit(app):
    uid = _register_get_id(app, "capped@example.com")
    admin = _admin_client(app)

    set_resp = admin.request(
        "PATCH",
        f"/api/v1/admin/users/{uid}/limit",
        json={"daily_limit_override": 1_000_000},
        headers=_csrf(admin),
    )
    assert set_resp.status_code == 200
    assert admin.get(f"/api/v1/admin/users/{uid}").json()["daily_limit"] == 1_000_000

    clear = admin.request("PATCH", f"/api/v1/admin/users/{uid}/limit", json={"daily_limit_override": None}, headers=_csrf(admin))
    assert clear.status_code == 200
    assert admin.get(f"/api/v1/admin/users/{uid}").json()["daily_limit"] == 250_000


def test_reset_all_usage_and_audit_trail(app):
    _register(app, "a@example.com")
    uid = _register_get_id(app, "b@example.com")
    admin = _admin_client(app)  # 3 users total

    # Do a couple of auditable actions.
    admin.request("POST", f"/api/v1/admin/users/{uid}/reset-usage", headers=_csrf(admin))
    resp = admin.request("POST", "/api/v1/admin/reset-all-usage", headers=_csrf(admin))
    assert resp.status_code == 200
    assert "3 users" in resp.json()["message"]

    audit = admin.get("/api/v1/admin/audit").json()
    actions = {row["action"] for row in audit["data"]}
    assert {"reset-usage", "reset-all-usage"} <= actions
    assert audit["total"] >= 2


def test_activity_feed_lists_runs(app):
    from datetime import UTC, datetime

    uid = _register_get_id(app, "runner@example.com")
    _insert_run(app, run_id="rx", user_id=uid, tokens=1234, when=datetime.now(UTC))
    admin = _admin_client(app)
    feed = admin.get("/api/v1/admin/activity").json()["data"]
    assert any(r["run_id"] == "rx" and r["email"] == "runner@example.com" for r in feed)


def test_controls_forbidden_for_regular_user(app):
    uid = _register_get_id(app, "victim@example.com")
    attacker = TestClient(app)
    attacker.post("/api/v1/auth/register", json={"email": "attacker@example.com", "password": _PASSWORD})
    r = attacker.request("POST", f"/api/v1/admin/users/{uid}/reset-usage", headers=_csrf(attacker))
    assert r.status_code == 403
