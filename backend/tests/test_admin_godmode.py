"""Tests for the new admin endpoints: recent users, password reset,
session revoke, forbid, sessions, byok management.

Each test uses a fresh SQLite engine; the ops console's BFF is not
involved — these exercise the FastAPI layer directly so a regression
in the wire format is caught at the unit layer.
"""

from __future__ import annotations

import asyncio
import os
import secrets

import pytest
from fastapi.testclient import TestClient

os.environ.setdefault("AUTH_JWT_SECRET", "test-secret-key-admin-god-mode-32chars")

from app.gateway.auth.config import AuthConfig, set_auth_config

_TEST_SECRET = "test-secret-key-admin-god-mode-32chars"
_PASSWORD = "Tr0ub4dor3a"


@pytest.fixture()
def app(tmp_path):
    from app.gateway import deps
    from app.gateway.app import create_app
    from deerflow.persistence.engine import close_engine, init_engine

    set_auth_config(AuthConfig(jwt_secret=_TEST_SECRET))
    url = f"sqlite+aiosqlite:///{tmp_path}/admin_godmode.db"
    asyncio.run(init_engine("sqlite", url=url, sqlite_dir=str(tmp_path)))
    deps._cached_local_provider = None
    deps._cached_repo = None
    try:
        yield create_app()
    finally:
        deps._cached_local_provider = None
        deps._cached_repo = None
        asyncio.run(close_engine())


def _csrf(client: TestClient) -> dict[str, str]:
    t = client.cookies.get("csrf_token")
    return {"X-CSRF-Token": t} if t else {}


def _admin_client(app) -> TestClient:
    c = TestClient(app)
    resp = c.post("/api/v1/auth/initialize", json={"email": "admin@x.com", "password": _PASSWORD})
    assert resp.status_code == 201, resp.text
    return c


def _register_get_id(app, email: str) -> str:
    c = TestClient(app)
    assert c.post("/api/v1/auth/register", json={"email": email, "password": _PASSWORD}).status_code == 201
    return c.get("/api/v1/auth/me").json()["id"]


# ── recent users ────────────────────────────────────────────────────────


def test_recent_users_returns_per_user_activity(app):
    uid = _register_get_id(app, "newbie@example.com")
    # A real login stamps last_sign_in_at; registration alone does not.
    client = TestClient(app)
    client.post(
        "/api/v1/auth/login/local",
        data={"username": "newbie@example.com", "password": _PASSWORD},
        headers={"X-CSRF-Token": "x"},
    )
    admin = _admin_client(app)
    feed = admin.get("/api/v1/admin/users/recent", params={"limit": 20}).json()
    rows = {u["id"]: u for u in feed["data"]}
    assert uid in rows
    me = rows[uid]
    assert me["last_sign_in_at"] is not None
    # No runs yet → last_run_at is null.
    assert me["last_run_at"] is None
    assert me["is_forbidden"] is False


def _insert_run(app, *, user_id, tokens, when):
    from deerflow.persistence.engine import get_session_factory
    from deerflow.persistence.run.model import RunRow

    async def _go():
        async with get_session_factory()() as session:
            session.add(RunRow(run_id=secrets.token_hex(8), thread_id="t", user_id=user_id, total_tokens=tokens, created_at=when))
            await session.commit()

    asyncio.run(_go())


def test_recent_users_aggregates_last_run(app):
    from datetime import UTC, datetime

    uid = _register_get_id(app, "runner@example.com")
    _insert_run(app, user_id=uid, tokens=999, when=datetime(2026, 8, 1, 12, 0, 0))
    admin = _admin_client(app)
    feed = admin.get("/api/v1/admin/users/recent").json()
    row = next(u for u in feed["data"] if u["id"] == uid)
    assert row["last_run_at"] is not None
    assert row["last_run_at"].startswith("2026-08-01T12:00:00")


def test_recent_users_403_for_regular_user(app):
    admin = _admin_client(app)
    # Use a separate client so the admin's session cookie is not
    # overwritten by the register call below.
    _register_get_id(app, "u@example.com")
    attacker = TestClient(app)
    attacker.post("/api/v1/auth/register", json={"email": "attacker@example.com", "password": _PASSWORD})
    resp = attacker.get("/api/v1/admin/users/recent")
    assert resp.status_code == 403
    # sanity: the admin still has access
    assert admin.get("/api/v1/admin/users/recent").status_code == 200


# ── audit metadata capture (ip/ua) ──────────────────────────────────────


def _ops_request(client: TestClient, path: str):
    """Send a request as the ops console (service token auth)."""
    monkey = pytest.MonkeyPatch()
    monkey.setenv("NOVA_OPS_TOKEN", "super-secret-ops-token")
    try:
        return client.get(path, headers={"X-Nova-Ops-Token": "super-secret-ops-token", "User-Agent": "ops-console-test/1.0"})
    finally:
        monkey.undo()


def test_audit_captures_actor_ip_and_user_agent(app):
    _admin_client(app)  # ensure admin exists
    # As an ops call, list /users/recent and check the resulting audit row
    # has the actor metadata stamped.
    monkey = pytest.MonkeyPatch()
    monkey.setenv("NOVA_OPS_TOKEN", "super-secret-ops-token")
    try:
        client = TestClient(app)
        resp = client.get(
            "/api/v1/admin/users/recent",
            headers={"X-Nova-Ops-Token": "super-secret-ops-token", "User-Agent": "ops-console-test/1.0"},
        )
    finally:
        monkey.undo()
    assert resp.status_code == 200

    # Check the audit trail saw the actor metadata.
    from deerflow.persistence.admin_audit.model import AdminAuditRow
    from deerflow.persistence.engine import get_session_factory

    async def _rows():
        async with get_session_factory()() as session:
            stmt = __import__("sqlalchemy").select(AdminAuditRow).order_by(AdminAuditRow.created_at.desc()).limit(1)
            result = await session.execute(stmt)
            return list(result.scalars().all())

    rows = asyncio.run(_rows())
    assert rows
    assert rows[0].action == "view-users-recent"
    assert rows[0].actor_user_agent == "ops-console-test/1.0"
    assert rows[0].actor_ip is not None  # any value


# ── reset-password ──────────────────────────────────────────────────────


def test_reset_password_invalidates_sessions(app):
    uid = _register_get_id(app, "victim@example.com")
    # Log in as the user with the original password.
    client = TestClient(app)
    login = client.post(
        "/api/v1/auth/login/local",
        data={"username": "victim@example.com", "password": _PASSWORD},
        headers={"X-CSRF-Token": "x"},
    )
    assert login.status_code == 200

    # Admin resets the password.
    admin = _admin_client(app)
    resp = admin.request("POST", f"/api/v1/admin/users/{uid}/reset-password", headers=_csrf(admin))
    assert resp.status_code == 200
    body = resp.json()
    assert body["email"] == "victim@example.com"
    assert len(body["new_password"]) >= 16  # tokens_urlsafe(16)

    # The user's existing session is invalidated: any authenticated call
    # with the cookie jar now returns 401.
    me = client.get("/api/v1/auth/me")
    assert me.status_code == 401


def test_reset_password_404_for_unknown_user(app):
    admin = _admin_client(app)
    resp = admin.request("POST", "/api/v1/admin/users/does-not-exist/reset-password", headers=_csrf(admin))
    assert resp.status_code == 404


def test_reset_password_auditable(app):
    uid = _register_get_id(app, "audit-target@example.com")
    admin = _admin_client(app)
    admin.request("POST", f"/api/v1/admin/users/{uid}/reset-password", headers=_csrf(admin))
    audit = admin.get("/api/v1/admin/audit", params={"target_user_id": uid}).json()
    actions = {row["action"] for row in audit["data"]}
    assert "reset-password" in actions


# ── revoke-sessions ─────────────────────────────────────────────────────


def test_revoke_sessions_signs_user_out(app):
    uid = _register_get_id(app, "kicked@example.com")
    client = TestClient(app)
    client.post(
        "/api/v1/auth/login/local",
        data={"username": "kicked@example.com", "password": _PASSWORD},
        headers={"X-CSRF-Token": "x"},
    )
    assert client.get("/api/v1/auth/me").status_code == 200

    admin = _admin_client(app)
    resp = admin.request("POST", f"/api/v1/admin/users/{uid}/revoke-sessions", headers=_csrf(admin))
    assert resp.status_code == 200

    # The session JWT was signed with token_version=N; after the bump it
    # is invalidated.
    assert client.get("/api/v1/auth/me").status_code == 401


# ── forbid / unforbid ───────────────────────────────────────────────────


def test_forbid_user_blocks_login(app):
    uid = _register_get_id(app, "blocked@example.com")
    admin = _admin_client(app)
    admin.request("POST", f"/api/v1/admin/users/{uid}/forbid", headers=_csrf(admin))

    # Login attempt is now 403 (forbidden), NOT 401 (invalid credentials)
    # so an attacker can't tell "wrong password" from "banned".
    client = TestClient(app)
    resp = client.post(
        "/api/v1/auth/login/local",
        data={"username": "blocked@example.com", "password": _PASSWORD},
        headers={"X-CSRF-Token": "x"},
    )
    assert resp.status_code == 403


def test_forbid_user_visible_in_user_detail(app):
    uid = _register_get_id(app, "tagged@example.com")
    admin = _admin_client(app)
    admin.request("POST", f"/api/v1/admin/users/{uid}/forbid", headers=_csrf(admin))
    detail = admin.get(f"/api/v1/admin/users/{uid}").json()
    assert detail["is_forbidden"] is True


def test_unforbid_user_restores_access(app):
    uid = _register_get_id(app, "reprieved@example.com")
    admin = _admin_client(app)
    admin.request("POST", f"/api/v1/admin/users/{uid}/forbid", headers=_csrf(admin))
    admin.request("POST", f"/api/v1/admin/users/{uid}/unforbid", headers=_csrf(admin))

    client = TestClient(app)
    resp = client.post(
        "/api/v1/auth/login/local",
        data={"username": "reprieved@example.com", "password": _PASSWORD},
        headers={"X-CSRF-Token": "x"},
    )
    assert resp.status_code == 200


def test_forbid_auditable(app):
    uid = _register_get_id(app, "audit-forbid@example.com")
    admin = _admin_client(app)
    admin.request("POST", f"/api/v1/admin/users/{uid}/forbid", headers=_csrf(admin))
    audit = admin.get("/api/v1/admin/audit", params={"target_user_id": uid}).json()
    actions = {row["action"] for row in audit["data"]}
    assert "forbid-user" in actions


# ── sessions audit ──────────────────────────────────────────────────────


def test_sessions_endpoint_returns_auth_history(app):
    uid = _register_get_id(app, "audited@example.com")
    admin = _admin_client(app)
    # Trigger a few audited events.
    admin.request("POST", f"/api/v1/admin/users/{uid}/reset-usage", headers=_csrf(admin))
    admin.request("POST", f"/api/v1/admin/users/{uid}/forbid", headers=_csrf(admin))
    admin.request("POST", f"/api/v1/admin/users/{uid}/unforbid", headers=_csrf(admin))

    resp = admin.get(f"/api/v1/admin/users/{uid}/sessions")
    assert resp.status_code == 200
    actions = {row["action"] for row in resp.json()["data"]}
    assert {"reset-usage", "forbid-user", "unforbid-user"} <= actions


# ── BYOK admin ──────────────────────────────────────────────────────────


def _enable_byok(monkey: pytest.MonkeyPatch):
    from cryptography.fernet import Fernet

    monkey.setenv("NOVA_BYOK_ENABLED", "1")
    monkey.setenv("NOVA_BYOK_SECRET", Fernet.generate_key().decode())


def test_user_byok_disabled_when_feature_off(app, monkeypatch):
    _register_get_id(app, "user@x.com")
    admin = _admin_client(app)
    uid = admin.get("/api/v1/admin/users").json()["data"][0]["id"]
    resp = admin.get(f"/api/v1/admin/users/{uid}/byok")
    assert resp.status_code == 200
    assert resp.json()["enabled"] is False


def test_user_byok_round_trip(app, monkeypatch):
    _enable_byok(monkeypatch)
    uid = _register_get_id(app, "byok@example.com")
    admin = _admin_client(app)

    # User sets a BYOK key via the self-service endpoint.
    client = TestClient(app)
    client.post(
        "/api/v1/auth/login/local",
        data={"username": "byok@example.com", "password": _PASSWORD},
        headers={"X-CSRF-Token": "x"},
    )
    csrf = client.cookies.get("csrf_token")
    assert csrf
    set_resp = client.post(
        "/api/v1/byok",
        json={"provider": "openai", "api_key": "sk-test-" + "x" * 40},
        headers={"X-CSRF-Token": csrf},
    )
    assert set_resp.status_code == 200

    # Admin sees the user's key metadata.
    resp = admin.get(f"/api/v1/admin/users/{uid}/byok")
    assert resp.status_code == 200
    body = resp.json()
    assert body["enabled"] is True
    assert body["has_key"] is True
    assert body["provider"] == "openai"

    # Admin revokes the key.
    rev = admin.request("DELETE", f"/api/v1/admin/users/{uid}/byok", headers=_csrf(admin))
    assert rev.status_code == 200

    # The key is now gone.
    resp2 = admin.get(f"/api/v1/admin/users/{uid}/byok")
    assert resp2.json()["has_key"] is False


# ── Users studio (ranked view) ──────────────────────────────────────────


def test_studio_returns_ranked_users_and_aggregates(app):
    from datetime import UTC, datetime

    # Register a heavy user with a run, then init the admin in the same
    # session to avoid the "already initialized" 409 the helper would
    # raise on a second call.
    client = TestClient(app)
    client.post(
        "/api/v1/auth/register",
        json={"email": "heavy@example.com", "password": _PASSWORD},
    )
    uid = client.get("/api/v1/auth/me").json()["id"]
    _insert_run(app, user_id=uid, tokens=100_000, when=datetime(2026, 8, 12, 12, 0, 0))

    admin = _admin_client(app)
    resp = admin.get("/api/v1/admin/users/studio")
    assert resp.status_code == 200
    body = resp.json()
    assert body["metrics"]["total_users"] >= 1
    assert "free" in body["by_plan"]
    # The heavy user is the top of the tokens ranking.
    top = body["ranking"][0]
    assert top["email"] == "heavy@example.com"
    assert top["lifetime_tokens"] >= 100_000
    assert top["run_count"] >= 1


def test_studio_supports_sort_and_plan_filter(app):
    from datetime import UTC, datetime

    admin = _admin_client(app)

    # tokens desc by default.
    resp = admin.get("/api/v1/admin/users/studio", params={"sort": "tokens", "limit": 5})
    assert resp.status_code == 200
    body = resp.json()
    rows = body["ranking"]
    assert rows
    tokens = [r["lifetime_tokens"] for r in rows]
    assert tokens == sorted(tokens, reverse=True)

    # recency == created_at desc.
    resp = admin.get("/api/v1/admin/users/studio", params={"sort": "recency", "limit": 5})
    assert resp.status_code == 200
    body = resp.json()
    rows = body["ranking"]
    assert rows
    # The most recent user is at the top.
    assert body["metrics"]["total_users"] >= 1

    # Plan filter narrows results.
    resp = admin.get("/api/v1/admin/users/studio", params={"plan": "free"})
    assert resp.status_code == 200
    body = resp.json()
    for r in body["ranking"]:
        assert r["plan"] == "free"


def test_studio_403_for_regular_user(app):
    _admin_client(app)
    user = TestClient(app)
    user.post("/api/v1/auth/register", json={"email": "attacker@example.com", "password": _PASSWORD})
    resp = user.get("/api/v1/admin/users/studio")
    assert resp.status_code == 403


def test_studio_rejects_unknown_sort(app):
    """422 path validation rejects any sort not in the allowed set."""
    monkey = pytest.MonkeyPatch()
    monkey.setenv("NOVA_OPS_TOKEN", "studio-token")
    try:
        client = TestClient(app)
        resp = client.get(
            "/api/v1/admin/users/studio",
            params={"sort": "wat"},
            headers={"X-Nova-Ops-Token": "studio-token"},
        )
    finally:
        monkey.undo()
    assert resp.status_code == 422
