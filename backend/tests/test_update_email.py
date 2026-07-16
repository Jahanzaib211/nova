"""Tests for POST /api/v1/auth/update-email.

Covers the email-only change flow added for the account-settings screen:
re-authentication with the current password, uniqueness enforcement,
token_version bump (session stays valid via cookie re-issue), and the
no-op / unauthenticated paths.
"""

import asyncio
import os

import pytest
from fastapi.testclient import TestClient

os.environ.setdefault("AUTH_JWT_SECRET", "test-secret-key-update-email-min-32chars")

from app.gateway.auth.config import AuthConfig, set_auth_config

_TEST_SECRET = "test-secret-key-update-email-min-32chars"
_PASSWORD = "Tr0ub4dor3a"


@pytest.fixture(autouse=True)
def _setup_auth(tmp_path):
    """Fresh SQLite engine + auth config + provider cache per test."""
    from app.gateway import deps
    from deerflow.persistence.engine import close_engine, init_engine

    set_auth_config(AuthConfig(jwt_secret=_TEST_SECRET))
    url = f"sqlite+aiosqlite:///{tmp_path}/update_email.db"
    asyncio.run(init_engine("sqlite", url=url, sqlite_dir=str(tmp_path)))
    deps._cached_local_provider = None
    deps._cached_repo = None
    try:
        yield
    finally:
        deps._cached_local_provider = None
        deps._cached_repo = None
        asyncio.run(close_engine())


@pytest.fixture()
def client(_setup_auth):
    from app.gateway.app import create_app

    set_auth_config(AuthConfig(jwt_secret=_TEST_SECRET))
    yield TestClient(create_app())


def _register(client: TestClient, email: str) -> None:
    resp = client.post("/api/v1/auth/register", json={"email": email, "password": _PASSWORD})
    assert resp.status_code == 201, resp.text


def _csrf_headers(client: TestClient) -> dict[str, str]:
    """Echo the csrf_token cookie as the X-CSRF-Token header (double-submit).

    register/login/initialize set the csrf_token cookie on their response,
    so the TestClient holds it after a successful _register.
    """
    token = client.cookies.get("csrf_token")
    return {"X-CSRF-Token": token} if token else {}


def _post_update_email(client: TestClient, current_password: str, new_email: str):
    return client.post(
        "/api/v1/auth/update-email",
        json={"current_password": current_password, "new_email": new_email},
        headers=_csrf_headers(client),
    )


def test_update_email_changes_email_and_keeps_session(client):
    """Correct password → email updated, /me reflects it, cookie re-issued."""
    _register(client, "old@example.com")

    resp = _post_update_email(client, _PASSWORD, "new@example.com")
    assert resp.status_code == 200, resp.text
    assert resp.json()["email"] == "new@example.com"
    # Session survives the token_version bump because a fresh cookie is set.
    assert "access_token" in resp.cookies

    me = client.get("/api/v1/auth/me")
    assert me.status_code == 200
    assert me.json()["email"] == "new@example.com"


def test_update_email_wrong_password_rejected(client):
    """Incorrect current password → 400, email unchanged."""
    _register(client, "keep@example.com")

    resp = _post_update_email(client, "WrongPass123", "hacker@example.com")
    assert resp.status_code == 400
    assert client.get("/api/v1/auth/me").json()["email"] == "keep@example.com"


def test_update_email_duplicate_rejected(client):
    """Changing to an email already in use → 400 email_already_exists."""
    _register(client, "first@example.com")
    # Log the first session out of the way by registering the second user
    # (register re-issues the cookie for the newly created account).
    _register(client, "second@example.com")

    # The active session is now "second"; try to steal "first"'s email.
    resp = _post_update_email(client, _PASSWORD, "first@example.com")
    assert resp.status_code == 400
    assert resp.json()["detail"]["code"] == "email_already_exists"


def test_update_email_same_email_is_noop(client):
    """Submitting the current email → 200, no error, email unchanged."""
    _register(client, "same@example.com")

    resp = _post_update_email(client, _PASSWORD, "same@example.com")
    assert resp.status_code == 200
    assert resp.json()["email"] == "same@example.com"


def test_update_email_requires_auth(client):
    """Valid CSRF token but no session cookie → 401 (auth layer, not CSRF)."""
    _register(client, "session@example.com")
    # Keep the csrf_token cookie, drop only the session — isolates the auth check.
    client.cookies.delete("access_token")

    resp = _post_update_email(client, _PASSWORD, "nobody@example.com")
    assert resp.status_code == 401

