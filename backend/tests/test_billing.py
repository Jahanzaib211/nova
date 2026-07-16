"""Tests for Nova Plus billing: the webhook event applier, flag gating,
the billing endpoints (503 when unconfigured), and the admin plan grant.
"""

import asyncio
import os
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

os.environ.setdefault("AUTH_JWT_SECRET", "test-secret-key-billing-min-32-chars!!!!")

from app.gateway import billing
from app.gateway.auth.config import AuthConfig, set_auth_config

_TEST_SECRET = "test-secret-key-billing-min-32-chars!!!!"
_PASSWORD = "Tr0ub4dor3a"


def _user(**kw):
    base = dict(
        id="u1",
        email="u@example.com",
        plan="free",
        plan_status=None,
        stripe_customer_id=None,
        stripe_subscription_id=None,
        plan_renews_at=None,
    )
    base.update(kw)
    return SimpleNamespace(**base)


class FakeProvider:
    def __init__(self, *users):
        self.users = {str(u.id): u for u in users}

    async def get_user(self, uid):
        return self.users.get(str(uid))

    async def get_user_by_stripe_customer_id(self, cid):
        return next((u for u in self.users.values() if u.stripe_customer_id == cid), None)

    async def update_user(self, u):
        self.users[str(u.id)] = u
        return u


# ── Flag gating ──────────────────────────────────────────────────────────


def test_billing_disabled_by_default(monkeypatch):
    monkeypatch.delenv("STRIPE_SECRET_KEY", raising=False)
    assert billing.billing_enabled() is False


def test_billing_enabled_with_secret(monkeypatch):
    monkeypatch.setenv("STRIPE_SECRET_KEY", "sk_test_123")
    assert billing.billing_enabled() is True


# ── Webhook event application (pure, dict-based) ─────────────────────────


def test_apply_checkout_completed_upgrades_to_plus():
    user = _user()
    provider = FakeProvider(user)
    event = {
        "type": "checkout.session.completed",
        "data": {"object": {"customer": "cus_1", "subscription": "sub_1", "metadata": {"nova_user_id": "u1"}}},
    }
    updated = asyncio.run(billing.apply_event(event, provider))
    assert updated is True
    assert user.plan == "plus"
    assert user.plan_status == "active"
    assert user.stripe_customer_id == "cus_1"
    assert user.stripe_subscription_id == "sub_1"


def test_apply_subscription_updated_active_sets_renewal():
    user = _user(stripe_customer_id="cus_1", plan="plus")
    provider = FakeProvider(user)
    event = {
        "type": "customer.subscription.updated",
        "data": {"object": {"customer": "cus_1", "id": "sub_1", "status": "active", "current_period_end": 1_800_000_000}},
    }
    updated = asyncio.run(billing.apply_event(event, provider))
    assert updated is True
    assert user.plan == "plus"
    assert user.plan_status == "active"
    assert user.plan_renews_at is not None


def test_apply_subscription_deleted_downgrades_to_free():
    user = _user(stripe_customer_id="cus_1", plan="plus", plan_status="active", stripe_subscription_id="sub_1")
    provider = FakeProvider(user)
    event = {
        "type": "customer.subscription.deleted",
        "data": {"object": {"customer": "cus_1", "id": "sub_1", "status": "canceled"}},
    }
    updated = asyncio.run(billing.apply_event(event, provider))
    assert updated is True
    assert user.plan == "free"
    assert user.plan_status == "canceled"
    assert user.stripe_subscription_id is None


def test_apply_past_due_keeps_plan_marks_status():
    user = _user(stripe_customer_id="cus_1", plan="plus", plan_status="active")
    provider = FakeProvider(user)
    event = {
        "type": "customer.subscription.updated",
        "data": {"object": {"customer": "cus_1", "id": "sub_1", "status": "past_due"}},
    }
    asyncio.run(billing.apply_event(event, provider))
    assert user.plan_status == "past_due"


def test_apply_unknown_event_ignored():
    provider = FakeProvider(_user())
    assert asyncio.run(billing.apply_event({"type": "invoice.paid", "data": {"object": {}}}, provider)) is False


def test_apply_checkout_unknown_user_ignored():
    provider = FakeProvider()  # no users
    event = {"type": "checkout.session.completed", "data": {"object": {"customer": "cus_x", "metadata": {}}}}
    assert asyncio.run(billing.apply_event(event, provider)) is False


# ── Endpoints ────────────────────────────────────────────────────────────


@pytest.fixture()
def app(tmp_path, monkeypatch):
    monkeypatch.delenv("STRIPE_SECRET_KEY", raising=False)
    from app.gateway import deps
    from app.gateway.app import create_app
    from deerflow.persistence.engine import close_engine, init_engine

    set_auth_config(AuthConfig(jwt_secret=_TEST_SECRET))
    url = f"sqlite+aiosqlite:///{tmp_path}/billing.db"
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
    token = client.cookies.get("csrf_token")
    return {"X-CSRF-Token": token} if token else {}


def test_billing_status_reports_disabled(app):
    client = TestClient(app)
    client.post("/api/v1/auth/register", json={"email": "u@example.com", "password": _PASSWORD})
    body = client.get("/api/v1/billing").json()
    assert body["enabled"] is False
    assert body["plan"] == "free"


def test_checkout_503_when_disabled(app):
    client = TestClient(app)
    client.post("/api/v1/auth/register", json={"email": "u@example.com", "password": _PASSWORD})
    resp = client.post("/api/v1/billing/checkout", json={}, headers=_csrf(client))
    assert resp.status_code == 503


def test_webhook_503_when_disabled(app):
    # Public path, but returns 503 when billing isn't configured.
    resp = TestClient(app).post("/api/v1/billing/webhook", content=b"{}")
    assert resp.status_code == 503


# ── Admin plan grant ─────────────────────────────────────────────────────


def _admin_client(app) -> TestClient:
    c = TestClient(app)
    assert c.post("/api/v1/auth/initialize", json={"email": "admin@example.com", "password": _PASSWORD}).status_code == 201
    return c


def _register_user(app, email) -> str:
    c = TestClient(app)
    c.post("/api/v1/auth/register", json={"email": email, "password": _PASSWORD})
    return c.get("/api/v1/auth/me").json()["id"]


def test_admin_can_grant_plan(app):
    uid = _register_user(app, "member@example.com")
    admin = _admin_client(app)
    resp = admin.request(
        "PATCH",
        f"/api/v1/admin/users/{uid}/plan",
        json={"plan": "plus"},
        headers=_csrf(admin),
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["plan"] == "plus"
    assert resp.json()["plan_status"] == "admin_granted"


def test_admin_grant_rejects_invalid_plan(app):
    uid = _register_user(app, "member@example.com")
    admin = _admin_client(app)
    resp = admin.request(
        "PATCH",
        f"/api/v1/admin/users/{uid}/plan",
        json={"plan": "galaxy"},
        headers=_csrf(admin),
    )
    assert resp.status_code == 400


def test_admin_grant_forbidden_for_regular_user(app):
    uid = _register_user(app, "member@example.com")
    other = TestClient(app)
    other.post("/api/v1/auth/register", json={"email": "other@example.com", "password": _PASSWORD})
    resp = other.request(
        "PATCH",
        f"/api/v1/admin/users/{uid}/plan",
        json={"plan": "plus"},
        headers=_csrf(other),
    )
    assert resp.status_code == 403
