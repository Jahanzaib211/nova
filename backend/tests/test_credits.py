"""Tests for the Nova credit system: daily token limits, usage-from-runs,
the balance computation, and the /api/v1/credits endpoint.
"""

import asyncio
import os
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

os.environ.setdefault("AUTH_JWT_SECRET", "test-secret-key-credits-min-32-characters")

from app.gateway.auth.config import AuthConfig, set_auth_config
from app.gateway.credits import (
    DAILY_TOKEN_LIMITS,
    daily_limit_for_plan,
    get_balance,
    tokens_used_today,
)

_TEST_SECRET = "test-secret-key-credits-min-32-characters"
_PASSWORD = "Tr0ub4dor3a"


# ── Pure-function limits ─────────────────────────────────────────────────


def test_daily_limit_for_plan():
    assert daily_limit_for_plan("free") == 250_000
    assert daily_limit_for_plan("plus") == DAILY_TOKEN_LIMITS["plus"]
    assert daily_limit_for_plan("enterprise") == DAILY_TOKEN_LIMITS["enterprise"]
    # Unknown / None fall back to the free allowance.
    assert daily_limit_for_plan("mystery") == 250_000
    assert daily_limit_for_plan(None) == 250_000


# ── Usage computed from the runs table ───────────────────────────────────


@pytest.fixture()
def engine(tmp_path):
    from deerflow.persistence.engine import close_engine, get_session_factory, init_engine

    url = f"sqlite+aiosqlite:///{tmp_path}/credits.db"
    asyncio.run(init_engine("sqlite", url=url, sqlite_dir=str(tmp_path)))
    try:
        yield get_session_factory()
    finally:
        asyncio.run(close_engine())


def _insert_run(sf, *, run_id, user_id, tokens, created_at):
    from deerflow.persistence.run.model import RunRow

    async def _go():
        async with sf() as session:
            session.add(
                RunRow(
                    run_id=run_id,
                    thread_id="t-1",
                    user_id=user_id,
                    total_tokens=tokens,
                    created_at=created_at,
                )
            )
            await session.commit()

    asyncio.run(_go())


def test_tokens_used_today_sums_only_todays_runs(engine):
    now = datetime(2026, 7, 15, 12, 0, tzinfo=UTC)
    yesterday = now - timedelta(days=1)

    _insert_run(engine, run_id="r-today-1", user_id="u1", tokens=30_000, created_at=now)
    _insert_run(engine, run_id="r-today-2", user_id="u1", tokens=20_000, created_at=now.replace(hour=1))
    _insert_run(engine, run_id="r-yesterday", user_id="u1", tokens=99_000, created_at=yesterday)
    _insert_run(engine, run_id="r-other-user", user_id="u2", tokens=77_000, created_at=now)

    used = asyncio.run(tokens_used_today("u1", session_factory=engine, now=now))
    assert used == 50_000  # only u1's two runs from today


def test_get_balance_computes_remaining(engine):
    now = datetime(2026, 7, 15, 9, 0, tzinfo=UTC)
    _insert_run(engine, run_id="r1", user_id="u1", tokens=200_000, created_at=now)

    user = SimpleNamespace(id="u1", plan="free")
    bal = asyncio.run(get_balance(user, session_factory=engine, now=now))
    assert bal.daily_limit == 250_000
    assert bal.used == 200_000
    assert bal.remaining == 50_000
    assert bal.exhausted is False


def test_get_balance_clamps_and_marks_exhausted(engine):
    now = datetime(2026, 7, 15, 9, 0, tzinfo=UTC)
    _insert_run(engine, run_id="r1", user_id="u1", tokens=300_000, created_at=now)

    user = SimpleNamespace(id="u1", plan="free")
    bal = asyncio.run(get_balance(user, session_factory=engine, now=now))
    assert bal.remaining == 0  # clamped, never negative
    assert bal.exhausted is True


def test_get_balance_adds_bonus(engine):
    now = datetime(2026, 7, 15, 9, 0, tzinfo=UTC)
    user = SimpleNamespace(id="u-bonus", plan="free")
    bal = asyncio.run(get_balance(user, session_factory=engine, now=now, bonus_daily_tokens=100_000))
    assert bal.daily_limit == 350_000
    assert bal.remaining == 350_000
    assert bal.bonus_daily_tokens == 100_000


def test_daily_limit_override_wins_over_plan(engine):
    """An operator-set custom limit replaces the plan allowance entirely."""
    now = datetime(2026, 7, 15, 9, 0, tzinfo=UTC)
    user = SimpleNamespace(id="u-ovr", plan="free", daily_limit_override=1_000_000, credit_usage_reset_at=None)
    bal = asyncio.run(get_balance(user, session_factory=engine, now=now, bonus_daily_tokens=0))
    assert bal.daily_limit == 1_000_000
    # Override can also shrink below the plan limit.
    user_small = SimpleNamespace(id="u-ovr2", plan="plus", daily_limit_override=10_000, credit_usage_reset_at=None)
    bal2 = asyncio.run(get_balance(user_small, session_factory=engine, now=now, bonus_daily_tokens=0))
    assert bal2.daily_limit == 10_000


def test_usage_reset_marker_restores_allowance(engine):
    """Runs before the operator reset marker no longer count against today."""
    now = datetime(2026, 7, 15, 12, 0, tzinfo=UTC)
    reset = datetime(2026, 7, 15, 10, 0, tzinfo=UTC)
    # 240k burned at 09:00 (before reset), 5k at 11:00 (after reset).
    _insert_run(engine, run_id="r-before", user_id="u-rst", tokens=240_000, created_at=now.replace(hour=9))
    _insert_run(engine, run_id="r-after", user_id="u-rst", tokens=5_000, created_at=now.replace(hour=11))

    user = SimpleNamespace(id="u-rst", plan="free", daily_limit_override=None, credit_usage_reset_at=reset)
    bal = asyncio.run(get_balance(user, session_factory=engine, now=now, bonus_daily_tokens=0))
    assert bal.used == 5_000
    assert bal.remaining == 245_000

    # A reset marker from a previous day is ignored (midnight wins).
    stale = SimpleNamespace(id="u-rst", plan="free", daily_limit_override=None, credit_usage_reset_at=datetime(2026, 7, 10, tzinfo=UTC))
    bal_stale = asyncio.run(get_balance(stale, session_factory=engine, now=now, bonus_daily_tokens=0))
    assert bal_stale.used == 245_000


# ── /api/v1/credits endpoint ─────────────────────────────────────────────


@pytest.fixture()
def client(tmp_path):
    from app.gateway import deps
    from app.gateway.app import create_app
    from deerflow.persistence.engine import close_engine, init_engine

    set_auth_config(AuthConfig(jwt_secret=_TEST_SECRET))
    url = f"sqlite+aiosqlite:///{tmp_path}/credits_api.db"
    asyncio.run(init_engine("sqlite", url=url, sqlite_dir=str(tmp_path)))
    deps._cached_local_provider = None
    deps._cached_repo = None
    try:
        yield TestClient(create_app())
    finally:
        deps._cached_local_provider = None
        deps._cached_repo = None
        asyncio.run(close_engine())


def test_credits_endpoint_for_new_user(client):
    """A fresh free user sees the full 250k allowance, wall not hit."""
    client.post("/api/v1/auth/register", json={"email": "u@example.com", "password": _PASSWORD})
    resp = client.get("/api/v1/credits")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["plan"] == "free"
    assert body["daily_limit"] == 250_000
    assert body["used"] == 0
    assert body["remaining"] == 250_000
    assert body["unlimited"] is False
    assert body["exhausted"] is False


def test_credits_endpoint_admin_is_unlimited(client):
    """Admins/operators are exempt from the wall (unlimited=True)."""
    client.post("/api/v1/auth/initialize", json={"email": "admin@example.com", "password": _PASSWORD})
    resp = client.get("/api/v1/credits")
    assert resp.status_code == 200, resp.text
    assert resp.json()["unlimited"] is True


def test_credits_endpoint_requires_auth(client):
    resp = client.get("/api/v1/credits")
    assert resp.status_code == 401


# ── The wall: start_run blocks exhausted end users with 402 ──────────────


@pytest.mark.asyncio
async def test_start_run_blocks_exhausted_user(monkeypatch):
    """An end user with no remaining credits gets 402 before any run starts."""
    from unittest.mock import AsyncMock, MagicMock

    from fastapi import HTTPException

    from app.gateway import services
    from app.gateway.credits import CreditBalance

    thread_store = MagicMock()
    thread_store.check_access = AsyncMock(return_value=True)
    run_ctx = SimpleNamespace(thread_store=thread_store)

    monkeypatch.setattr(services, "get_stream_bridge", lambda r: MagicMock())
    monkeypatch.setattr(services, "get_run_manager", lambda r: MagicMock())
    monkeypatch.setattr(services, "get_run_context", lambda r: run_ctx)
    monkeypatch.setattr(services, "get_trusted_internal_owner_user_id", lambda r: None)
    # Force an exhausted balance regardless of DB state.
    exhausted = CreditBalance(plan="free", daily_limit=250_000, used=250_000, remaining=0)
    monkeypatch.setattr("app.gateway.credits.get_balance", AsyncMock(return_value=exhausted))
    monkeypatch.setenv("NOVA_CREDITS_ENFORCED", "1")  # wall is opt-in

    request = SimpleNamespace(
        state=SimpleNamespace(user=SimpleNamespace(id="u1", system_role="user", plan="free"))
    )
    body = SimpleNamespace(
        on_disconnect="continue",
        context={},
        assistant_id=None,
        metadata={},
        input={},
        config={},
        multitask_strategy="reject",
    )

    with pytest.raises(HTTPException) as exc:
        await services.start_run(body, "t1", request)
    assert exc.value.status_code == 402
    assert exc.value.detail["code"] == "daily_limit_reached"


@pytest.mark.asyncio
async def test_start_run_does_not_wall_admin(monkeypatch):
    """Admins skip the credit check entirely (get_balance never consulted)."""
    from unittest.mock import AsyncMock, MagicMock

    from app.gateway import services

    thread_store = MagicMock()
    thread_store.check_access = AsyncMock(return_value=True)
    run_ctx = SimpleNamespace(thread_store=thread_store)

    monkeypatch.setattr(services, "get_stream_bridge", lambda r: MagicMock())
    monkeypatch.setattr(services, "get_run_manager", lambda r: MagicMock())
    monkeypatch.setattr(services, "get_run_context", lambda r: run_ctx)
    # Non-null owner so the set_current_user short-circuit below is reached.
    monkeypatch.setattr(services, "get_trusted_internal_owner_user_id", lambda r: "owner1")

    called = {"balance": False}

    async def _boom(*_a, **_k):
        called["balance"] = True
        raise AssertionError("get_balance must not run for admins")

    monkeypatch.setattr("app.gateway.credits.get_balance", _boom)
    # Short-circuit right after the credit gate so we don't need full run infra.
    sentinel = RuntimeError("reached create_or_reject")

    def _stop(*_a, **_k):
        raise sentinel

    monkeypatch.setattr(services, "set_current_user", _stop)

    request = SimpleNamespace(
        state=SimpleNamespace(user=SimpleNamespace(id="admin1", system_role="admin", plan="free"))
    )
    body = SimpleNamespace(
        on_disconnect="continue",
        context={},
        assistant_id=None,
        metadata={},
        input={},
        config={},
        multitask_strategy="reject",
    )

    # Admin path skips the wall and proceeds; our sentinel proves we got past it.
    with pytest.raises(RuntimeError, match="reached create_or_reject"):
        await services.start_run(body, "t1", request)
    assert called["balance"] is False

