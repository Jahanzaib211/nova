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


def test_audit_filterable_by_target_user(app):
    uid_a = _register_get_id(app, "audit-a@example.com")
    uid_b = _register_get_id(app, "audit-b@example.com")
    admin = _admin_client(app)  # 3 users total (a, b, admin)

    admin.request("POST", f"/api/v1/admin/users/{uid_a}/reset-usage", headers=_csrf(admin))
    admin.request("POST", f"/api/v1/admin/users/{uid_b}/reset-usage", headers=_csrf(admin))
    # reset-all-usage records target_user_id=None — must not leak into a
    # user-scoped filter.
    admin.request("POST", "/api/v1/admin/reset-all-usage", headers=_csrf(admin))

    scoped = admin.get("/api/v1/admin/audit", params={"target_user_id": uid_a}).json()
    # The reset-usage row is the only mutating action on uid_a. The
    # remaining "view-audit" row stamped by the line above carries the
    # same target_user_id (the filter value), so the count is 2.
    assert scoped["total"] == 2
    actions = {row["action"] for row in scoped["data"]}
    assert "reset-usage" in actions
    assert all(row["target_user_id"] == uid_a for row in scoped["data"])

    unscoped = admin.get("/api/v1/admin/audit").json()
    assert unscoped["total"] >= 3


def test_audit_filter_forbidden_for_regular_user(app):
    uid = _register_get_id(app, "audit-target@example.com")
    attacker = TestClient(app)
    attacker.post("/api/v1/auth/register", json={"email": "audit-snoop@example.com", "password": _PASSWORD})

    resp = attacker.get("/api/v1/admin/audit", params={"target_user_id": uid})
    assert resp.status_code == 403


def test_activity_feed_lists_runs(app):
    from datetime import UTC, datetime

    uid = _register_get_id(app, "runner@example.com")
    _insert_run(app, run_id="rx", user_id=uid, tokens=1234, when=datetime.now(UTC))
    admin = _admin_client(app)
    feed = admin.get("/api/v1/admin/activity").json()["data"]
    assert any(r["run_id"] == "rx" and r["email"] == "runner@example.com" for r in feed)


def _utc_offset_seconds(iso: str) -> int:
    """Parse an ISO string and return its UTC offset in seconds.

    Browsers parse an offset-less ISO datetime as *local* time, so every
    timestamp the ops console renders would drift by the viewer's offset.
    The admin API must emit tz-aware UTC. Accept both ``Z`` and ``+00:00``.
    """
    from datetime import datetime, timezone

    assert isinstance(iso, str) and iso, f"expected a non-empty ISO string, got {iso!r}"
    dt = datetime.fromisoformat(iso.replace("Z", "+00:00"))
    assert dt.tzinfo is not None, f"created_at must be tz-aware, got {iso!r}"
    return dt.utcoffset().total_seconds()


def test_admin_timestamps_are_tz_aware_utc(app):
    """Regression: naive UTC datetimes must never reach the console.

    SQLite ``DateTime(timezone=True)`` columns come back naive; before the
    fix Pydantic serialized them offset-less and the browser reinterpreted
    them as local time. Every admin surface must emit ``+00:00``/``Z``.
    """
    from datetime import UTC, datetime

    uid = _register_get_id(app, "tz@example.com")
    # Store naive UTC to simulate the SQLite round-trip that strips tzinfo.
    _insert_run(app, run_id="tz-run", user_id=uid, tokens=100, when=datetime(2026, 8, 11, 16, 38, 13))
    admin = _admin_client(app)

    users = admin.get("/api/v1/admin/users").json()["data"]
    row = next(u for u in users if u["email"] == "tz@example.com")
    assert _utc_offset_seconds(row["created_at"]) == 0

    detail = admin.get(f"/api/v1/admin/users/{uid}").json()
    assert _utc_offset_seconds(detail["created_at"]) == 0
    assert detail["credit_usage_reset_at"] is None
    assert _utc_offset_seconds(detail["recent_runs"][0]["created_at"]) == 0

    feed = admin.get("/api/v1/admin/activity").json()["data"]
    assert _utc_offset_seconds(feed[0]["created_at"]) == 0

    # Trigger an audited action so the trail has a row to inspect.
    admin.request("POST", f"/api/v1/admin/users/{uid}/reset-usage", headers=_csrf(admin))
    audit = admin.get("/api/v1/admin/audit").json()["data"]
    assert audit, "expected at least one audit row after reset-usage"
    assert _utc_offset_seconds(audit[0]["created_at"]) == 0


def test_controls_forbidden_for_regular_user(app):
    uid = _register_get_id(app, "victim@example.com")
    attacker = TestClient(app)
    attacker.post("/api/v1/auth/register", json={"email": "attacker@example.com", "password": _PASSWORD})
    r = attacker.request("POST", f"/api/v1/admin/users/{uid}/reset-usage", headers=_csrf(attacker))
    assert r.status_code == 403


# ── Per-user conversation content (ops ability to read message text) ────


def _make_thread(app, *, thread_id, user_id, display_name):
    from datetime import UTC, datetime

    from deerflow.persistence.engine import get_session_factory
    from deerflow.persistence.thread_meta.model import ThreadMetaRow

    async def _go():
        async with get_session_factory()() as session:
            session.add(
                ThreadMetaRow(
                    thread_id=thread_id,
                    user_id=user_id,
                    display_name=display_name,
                    status="idle",
                    created_at=datetime.now(UTC),
                    updated_at=datetime.now(UTC),
                )
            )
            await session.commit()

    asyncio.run(_go())


def _write_message(app, *, thread_id, run_id, content, seq=1):
    from deerflow.persistence.engine import get_session_factory
    from deerflow.persistence.models.run_event import RunEventRow

    async def _go():
        async with get_session_factory()() as session:
            session.add(
                RunEventRow(
                    thread_id=thread_id,
                    run_id=run_id,
                    event_type="human_message",
                    category="message",
                    content=content,
                    seq=seq,
                )
            )
            await session.commit()

    asyncio.run(_go())


def test_list_user_conversations_returns_threads(app):
    uid = _register_get_id(app, "chatty@example.com")
    _make_thread(app, thread_id="th1", user_id=uid, display_name="First chat")
    admin = _admin_client(app)

    resp = admin.get(f"/api/v1/admin/users/{uid}/conversations")
    assert resp.status_code == 200, resp.text
    data = resp.json()["data"]
    assert len(data) == 1
    assert data[0]["thread_id"] == "th1"
    assert data[0]["display_name"] == "First chat"


def test_get_user_conversation_messages_returns_content(app):
    uid = _register_get_id(app, "reader@example.com")
    _make_thread(app, thread_id="th2", user_id=uid, display_name="Second chat")
    _write_message(app, thread_id="th2", run_id="r1", content="hello from the user")
    admin = _admin_client(app)

    resp = admin.get(f"/api/v1/admin/users/{uid}/conversations/th2/messages")
    assert resp.status_code == 200, resp.text
    data = resp.json()["data"]
    assert len(data) == 1
    assert data[0]["content"] == "hello from the user"


def test_conversation_messages_404_for_wrong_owner(app):
    uid = _register_get_id(app, "owner@example.com")
    other_uid = _register_get_id(app, "other@example.com")
    _make_thread(app, thread_id="th3", user_id=uid, display_name="Owner's chat")
    admin = _admin_client(app)

    resp = admin.get(f"/api/v1/admin/users/{other_uid}/conversations/th3/messages")
    assert resp.status_code == 404


def test_conversations_forbidden_for_regular_user(app):
    uid = _register_get_id(app, "target@example.com")
    attacker = TestClient(app)
    attacker.post("/api/v1/auth/register", json={"email": "snoop@example.com", "password": _PASSWORD})

    resp = attacker.get(f"/api/v1/admin/users/{uid}/conversations")
    assert resp.status_code == 403


def test_conversation_access_is_audited(app):
    uid = _register_get_id(app, "audited@example.com")
    _make_thread(app, thread_id="th4", user_id=uid, display_name="Audited chat")
    admin = _admin_client(app)

    admin.get(f"/api/v1/admin/users/{uid}/conversations")
    admin.get(f"/api/v1/admin/users/{uid}/conversations/th4/messages")

    audit = admin.get("/api/v1/admin/audit").json()
    actions = {row["action"] for row in audit["data"]}
    assert {"view-conversations", "view-conversation-messages"} <= actions


# ── Admin cancel another user's run ──────────────────────────────────────
#
# app.state.run_manager / run_service are normally populated by the ASGI
# lifespan handler, but entering it here (``with TestClient(app) as ...:``)
# would re-run init_engine_from_config and clobber this fixture's
# tmp_path-scoped SQLite engine set up above. Wire a real RunManager onto
# app.state directly instead, exactly like test_cancel_run_idempotent.py
# does for its own minimal test app — no full lifespan needed.


def _create_run(app, *, thread_id, user_id):
    from deerflow.runtime import RunManager
    from deerflow.services.implementations import RunServiceImpl

    if not hasattr(app.state, "run_manager"):
        app.state.run_manager = RunManager()
        app.state.run_service = RunServiceImpl(app.state.run_manager)

    return asyncio.run(app.state.run_manager.create(thread_id, user_id=user_id)).run_id


def test_admin_can_cancel_user_run(app):
    uid = _register_get_id(app, "stoppable@example.com")
    _make_thread(app, thread_id="th5", user_id=uid, display_name="Long task")
    run_id = _create_run(app, thread_id="th5", user_id=uid)
    admin = _admin_client(app)

    resp = admin.request(
        "POST",
        f"/api/v1/admin/users/{uid}/conversations/th5/runs/{run_id}/cancel",
        headers=_csrf(admin),
    )
    assert resp.status_code == 200, resp.text

    from deerflow.runtime import RunStatus

    record = asyncio.run(app.state.run_manager.get(run_id))
    assert record.status == RunStatus.interrupted


def test_admin_cancel_run_404_for_wrong_owner(app):
    uid = _register_get_id(app, "real-owner@example.com")
    other_uid = _register_get_id(app, "not-owner@example.com")
    _make_thread(app, thread_id="th6", user_id=uid, display_name="Owner's task")
    run_id = _create_run(app, thread_id="th6", user_id=uid)
    admin = _admin_client(app)

    resp = admin.request(
        "POST",
        f"/api/v1/admin/users/{other_uid}/conversations/th6/runs/{run_id}/cancel",
        headers=_csrf(admin),
    )
    assert resp.status_code == 404


def test_admin_cancel_run_404_for_unknown_run(app):
    uid = _register_get_id(app, "no-run@example.com")
    _make_thread(app, thread_id="th7", user_id=uid, display_name="Empty thread")
    _create_run(app, thread_id="th7", user_id=uid)  # wires app.state.run_manager/run_service
    admin = _admin_client(app)

    resp = admin.request(
        "POST",
        f"/api/v1/admin/users/{uid}/conversations/th7/runs/no-such-run/cancel",
        headers=_csrf(admin),
    )
    assert resp.status_code == 404


def test_admin_cancel_run_forbidden_for_regular_user(app):
    uid = _register_get_id(app, "target-user@example.com")
    _make_thread(app, thread_id="th8", user_id=uid, display_name="Task")
    run_id = _create_run(app, thread_id="th8", user_id=uid)

    attacker = TestClient(app)
    attacker.post("/api/v1/auth/register", json={"email": "not-admin@example.com", "password": _PASSWORD})

    resp = attacker.request(
        "POST",
        f"/api/v1/admin/users/{uid}/conversations/th8/runs/{run_id}/cancel",
        headers=_csrf(attacker),
    )
    assert resp.status_code == 403


def test_admin_cancel_run_is_audited(app):
    uid = _register_get_id(app, "audited-run@example.com")
    _make_thread(app, thread_id="th9", user_id=uid, display_name="Audited task")
    run_id = _create_run(app, thread_id="th9", user_id=uid)
    admin = _admin_client(app)

    resp = admin.request(
        "POST",
        f"/api/v1/admin/users/{uid}/conversations/th9/runs/{run_id}/cancel",
        headers=_csrf(admin),
    )
    assert resp.status_code == 200

    audit = admin.get("/api/v1/admin/audit").json()
    actions = {row["action"] for row in audit["data"]}
    assert "cancel-user-run" in actions


# ── Per-thread run list (powers the "Cancel run" UI button) ────────────


def _insert_run_persistent(app, *, run_id, thread_id, user_id, status="running", tokens=0):
    """Insert a RunRow directly into the runs table. The test helper
    ``_create_run`` instantiates runs in the in-memory RunManager, which
    the new endpoint doesn't query — this writes the SQL row the new
    endpoint actually reads."""
    from deerflow.persistence.engine import get_session_factory
    from deerflow.persistence.run.model import RunRow

    async def _go():
        async with get_session_factory()() as session:
            async with session.begin():
                session.add(
                    RunRow(
                        run_id=run_id,
                        thread_id=thread_id,
                        user_id=user_id,
                        total_tokens=tokens,
                        status=status,
                    )
                )

    asyncio.run(_go())
    return run_id


def test_admin_thread_runs_returns_runs_for_a_thread(app):
    uid = _register_get_id(app, "runner-list@example.com")
    _make_thread(app, thread_id="th-list", user_id=uid, display_name="Run list")
    rid_a = _insert_run_persistent(app, run_id="rid-a", thread_id="th-list", user_id=uid)
    rid_b = _insert_run_persistent(app, run_id="rid-b", thread_id="th-list", user_id=uid)
    admin = _admin_client(app)

    resp = admin.get(f"/api/v1/admin/users/{uid}/conversations/th-list/runs")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["thread_id"] == "th-list"
    assert rid_a in body["run_ids"]
    assert rid_b in body["run_ids"]
    assert len(body["statuses"]) == len(body["run_ids"])

    audit = admin.get("/api/v1/admin/audit").json()
    actions = {row["action"] for row in audit["data"]}
    assert "view-thread-runs" in actions


def test_admin_thread_runs_filters_by_status(app):
    uid = _register_get_id(app, "running-vs-done@example.com")
    _make_thread(app, thread_id="th-status", user_id=uid, display_name="Mixed")
    _insert_run_persistent(app, run_id="rid-running", thread_id="th-status", user_id=uid, status="running")
    _insert_run_persistent(app, run_id="rid-success", thread_id="th-status", user_id=uid, status="success")
    admin = _admin_client(app)

    resp = admin.get(
        f"/api/v1/admin/users/{uid}/conversations/th-status/runs",
        params={"status_filter": "running"},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert "rid-running" in body["run_ids"]
    assert "rid-success" not in body["run_ids"]


def test_admin_thread_runs_404_for_unknown_thread(app):
    uid = _register_get_id(app, "thread-404@example.com")
    admin = _admin_client(app)
    resp = admin.get(f"/api/v1/admin/users/{uid}/conversations/does-not-exist/runs")
    assert resp.status_code == 404


def test_admin_thread_runs_403_for_regular_user(app):
    uid = _register_get_id(app, "thread-priv@example.com")
    _make_thread(app, thread_id="th-priv", user_id=uid, display_name="Private")
    user = TestClient(app)
    user.post("/api/v1/auth/register", json={"email": "intruder@example.com", "password": _PASSWORD})
    resp = user.get(f"/api/v1/admin/users/{uid}/conversations/th-priv/runs")
    assert resp.status_code == 403


def test_admin_thread_runs_rejects_unknown_status_filter(app):
    uid = _register_get_id(app, "thread-filter@example.com")
    _make_thread(app, thread_id="th-filter", user_id=uid, display_name="Filter")
    admin = _admin_client(app)
    resp = admin.get(
        f"/api/v1/admin/users/{uid}/conversations/th-filter/runs",
        params={"status_filter": "made-up"},
    )
    assert resp.status_code == 422


# ── Admin visibility into user channel connections ───────────────────────


def _enable_channel_connections(app):
    from deerflow.config.channel_connections_config import ChannelConnectionsConfig

    app.state.channel_connections_config = ChannelConnectionsConfig.model_validate({"enabled": True, "telegram": {"enabled": True, "bot_username": "deerflow_bot"}})


def _create_connection(app, *, user_id, provider="telegram", external_account_id="ext-1"):
    from deerflow.persistence.channel_connections import ChannelConnectionRepository
    from deerflow.persistence.engine import get_session_factory

    repo = ChannelConnectionRepository(get_session_factory())
    row = asyncio.run(
        repo.upsert_connection(
            owner_user_id=user_id,
            provider=provider,
            external_account_id=external_account_id,
            status="connected",
        )
    )
    return row["id"]


def test_admin_can_list_user_channel_connections(app):
    uid = _register_get_id(app, "linked@example.com")
    _enable_channel_connections(app)
    _create_connection(app, user_id=uid)
    admin = _admin_client(app)

    resp = admin.get(f"/api/v1/admin/users/{uid}/channels")
    assert resp.status_code == 200, resp.text
    connections = resp.json()["connections"]
    assert len(connections) == 1
    assert connections[0]["provider"] == "telegram"
    assert connections[0]["external_account_id"] == "ext-1"


def test_admin_channel_connections_404_for_unknown_user(app):
    _enable_channel_connections(app)
    admin = _admin_client(app)

    resp = admin.get("/api/v1/admin/users/no-such-user/channels")
    assert resp.status_code == 404


def test_admin_channel_connections_forbidden_for_regular_user(app):
    uid = _register_get_id(app, "spied-on@example.com")
    _enable_channel_connections(app)
    _create_connection(app, user_id=uid)

    attacker = TestClient(app)
    attacker.post("/api/v1/auth/register", json={"email": "channel-snoop@example.com", "password": _PASSWORD})

    resp = attacker.get(f"/api/v1/admin/users/{uid}/channels")
    assert resp.status_code == 403


def test_admin_can_revoke_user_channel_connection(app):
    uid = _register_get_id(app, "revocable@example.com")
    _enable_channel_connections(app)
    connection_id = _create_connection(app, user_id=uid)
    admin = _admin_client(app)

    resp = admin.request(
        "DELETE",
        f"/api/v1/admin/users/{uid}/channels/{connection_id}",
        headers=_csrf(admin),
    )
    assert resp.status_code == 204, resp.text

    # list_connections returns all rows regardless of status (same behavior
    # as the user-facing GET /api/channels/connections) — revocation shows
    # up as a status flip, not row removal.
    remaining = admin.get(f"/api/v1/admin/users/{uid}/channels").json()["connections"]
    assert len(remaining) == 1
    assert remaining[0]["status"] == "revoked"


def test_admin_revoke_channel_connection_404_for_wrong_owner(app):
    uid = _register_get_id(app, "owner-of-connection@example.com")
    other_uid = _register_get_id(app, "not-connection-owner@example.com")
    _enable_channel_connections(app)
    connection_id = _create_connection(app, user_id=uid)
    admin = _admin_client(app)

    resp = admin.request(
        "DELETE",
        f"/api/v1/admin/users/{other_uid}/channels/{connection_id}",
        headers=_csrf(admin),
    )
    assert resp.status_code == 404

    # The connection must survive an admin request scoped to the wrong owner.
    remaining = admin.get(f"/api/v1/admin/users/{uid}/channels").json()["connections"]
    assert len(remaining) == 1


def test_admin_channel_connections_access_is_audited(app):
    uid = _register_get_id(app, "audited-channels@example.com")
    _enable_channel_connections(app)
    connection_id = _create_connection(app, user_id=uid)
    admin = _admin_client(app)

    admin.get(f"/api/v1/admin/users/{uid}/channels")
    admin.request("DELETE", f"/api/v1/admin/users/{uid}/channels/{connection_id}", headers=_csrf(admin))

    audit = admin.get("/api/v1/admin/audit").json()
    actions = {row["action"] for row in audit["data"]}
    assert {"view-channel-connections", "revoke-channel-connection"} <= actions
