"""Tests for the self-service password-reset flow (forgot-password email).

Covers the token service (one-shot, expiring, last-wins) and the
``/api/v1/auth/forgot-password`` + ``/api/v1/auth/reset-password``
endpoints, including the SMTP-disabled and SMTP-failure paths.
"""

from __future__ import annotations

import asyncio
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.gateway.auth.local_provider import LocalAuthProvider
from app.gateway.auth.password_reset import consume_token, create_reset_token
from app.gateway.auth.repositories.sqlite import SQLiteUserRepository
from app.gateway.routers import auth as auth_router
from deerflow.persistence.base import Base
from deerflow.persistence.password_reset_token.model import PasswordResetTokenRow


@pytest.fixture()
def sf():
    """In-memory SQLite session factory (registers all tables)."""
    engine = create_async_engine("sqlite+aiosqlite://")
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

    async def _setup() -> None:
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)

    asyncio.run(_setup())
    yield factory
    asyncio.run(engine.dispose())


def _provider(sf) -> LocalAuthProvider:
    return LocalAuthProvider(repository=SQLiteUserRepository(sf))


async def _make_user(sf, *, email: str = "user@example.com"):
    return await _provider(sf).create_user(email=email, password="good-password-1")


@contextmanager
def _authed_app(sf, *, email_config: dict | None = None):
    """Build an auth-router test app with real local provider wired to ``sf``.

    The router resolves ``get_app_config`` and ``get_local_provider`` at
    call time, so the patches must stay active for the duration of the
    request — hence the context manager.
    """
    app = FastAPI()
    app.include_router(auth_router.router)

    provider = _provider(sf)
    config = email_config or {
        "enabled": True,
        "host": "smtp.test",
        "port": 587,
        "from_email": "no-reply@test",
        "from_name": "Nova",
        "reset_link_base_url": "https://nova.test",
    }
    with (
        patch("deerflow.config.app_config.get_app_config") as mock_get,
        patch("app.gateway.routers.auth.get_local_provider", return_value=provider),
        patch("deerflow.persistence.engine.get_session_factory", return_value=sf),
    ):
        mock_get.return_value = SimpleNamespace(email=SimpleNamespace(**config))
        yield app, provider


def _client(app) -> TestClient:
    return TestClient(app)


# ── token service ────────────────────────────────────────────────────────


def test_token_roundtrip_and_single_use(sf):
    user = asyncio.run(_make_user(sf))
    token = asyncio.run(create_reset_token(sf, user_id=str(user.id)))

    assert len(token) >= 32
    assert asyncio.run(consume_token(sf, token=token)) == str(user.id)
    # One-shot: a replay must be rejected.
    assert asyncio.run(consume_token(sf, token=token)) is None


def test_unknown_token_rejected(sf):
    assert asyncio.run(consume_token(sf, token="totally-made-up")) is None


def test_new_token_invalidates_previous(sf):
    user = asyncio.run(_make_user(sf))
    first = asyncio.run(create_reset_token(sf, user_id=str(user.id)))
    second = asyncio.run(create_reset_token(sf, user_id=str(user.id)))

    assert asyncio.run(consume_token(sf, token=first)) is None
    assert asyncio.run(consume_token(sf, token=second)) == str(user.id)


def test_expired_token_rejected(sf):
    user = asyncio.run(_make_user(sf))
    token = asyncio.run(create_reset_token(sf, user_id=str(user.id)))

    async def _expire() -> None:
        async with sf() as session:
            row = (await session.execute(select(PasswordResetTokenRow).where(PasswordResetTokenRow.user_id == str(user.id)))).scalar_one()
            row.expires_at = datetime.now(UTC) - timedelta(seconds=1)
            await session.commit()

    asyncio.run(_expire())
    assert asyncio.run(consume_token(sf, token=token)) is None


# ── forgot-password endpoint ─────────────────────────────────────────────


def test_forgot_password_sends_email_and_resets(sf):
    user = asyncio.run(_make_user(sf))
    sent: dict = {}

    async def _fake_send_email(*, config, to, subject, html):
        sent.update(to=to, subject=subject, html=html)

    with _authed_app(sf) as (app, _):
        with patch("app.gateway.email.send_email", side_effect=_fake_send_email):
            response = _client(app).post("/api/v1/auth/forgot-password", json={"email": user.email})

    assert response.status_code == 200
    assert "reset link was sent" in response.json()["message"].lower()
    assert sent["to"] == user.email
    # The email body carries a reset link with the token.
    assert "/reset-password?token=" in sent["html"]

    token = sent["html"].split("token=")[1].split('"')[0]
    assert asyncio.run(consume_token(sf, token=token)) == str(user.id)


def test_forgot_password_does_not_leak_unknown_emails(sf):
    notified: list[str] = []

    async def _fake_send_email(*, config, to, subject, html):
        notified.append(to)

    with _authed_app(sf) as (app, _):
        with patch("app.gateway.email.send_email", side_effect=_fake_send_email):
            response = _client(app).post("/api/v1/auth/forgot-password", json={"email": "nobody@example.com"})

    assert response.status_code == 200
    assert notified == []


def test_forgot_password_disabled_reports_error(sf):
    with _authed_app(sf, email_config={"enabled": False}) as (app, _):
        response = _client(app).post("/api/v1/auth/forgot-password", json={"email": "user@example.com"})

    assert response.status_code == 400
    assert response.json()["detail"]["code"] == "password_reset_disabled"


def test_forgot_password_send_failure_invalidates_token(sf):
    user = asyncio.run(_make_user(sf))

    async def _boom(*, config, to, subject, html):
        raise RuntimeError("smtp down")

    with _authed_app(sf) as (app, _):
        with patch("app.gateway.email.send_email", side_effect=_boom):
            response = _client(app).post("/api/v1/auth/forgot-password", json={"email": user.email})

    assert response.status_code == 502

    async def _any_unconsumed() -> bool:
        async with sf() as session:
            rows = (await session.execute(select(PasswordResetTokenRow))).scalars().all()
            return any(not r.consumed for r in rows)

    assert asyncio.run(_any_unconsumed()) is False


# ── reset-password endpoint ──────────────────────────────────────────────


def test_reset_password_flow_end_to_end(sf):
    user = asyncio.run(_make_user(sf))
    token = asyncio.run(create_reset_token(sf, user_id=str(user.id)))

    with _authed_app(sf) as (app, _):
        response = _client(app).post(
            "/api/v1/auth/reset-password",
            json={"token": token, "new_password": "brand-new-pw-9"},
        )
        assert response.status_code == 200

        # The used token must not work again (same request context: the
        # session-factory patch has to stay live for the replay too).
        replay = _client(app).post(
            "/api/v1/auth/reset-password",
            json={"token": token, "new_password": "another-pw-9"},
        )
        assert replay.status_code == 400
        assert replay.json()["detail"]["code"] == "password_reset_token_invalid"

    updated = asyncio.run(_provider(sf).get_user(str(user.id)))
    assert updated.token_version == user.token_version + 1
    assert updated.password_hash != user.password_hash


def test_reset_password_rejects_weak_password(sf):
    user = asyncio.run(_make_user(sf))
    token = asyncio.run(create_reset_token(sf, user_id=str(user.id)))

    with _authed_app(sf) as (app, _):
        for weak in ("password", "short", "12345678"):
            response = _client(app).post(
                "/api/v1/auth/reset-password",
                json={"token": token, "new_password": weak},
            )
            assert response.status_code == 422

        # Strong password still consumes the token.
        ok = _client(app).post(
            "/api/v1/auth/reset-password",
            json={"token": token, "new_password": "proper-strong-9"},
        )
        assert ok.status_code == 200
