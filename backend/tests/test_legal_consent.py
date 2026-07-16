"""Tests for the legal / TOS consent endpoints and signup consent stamping.

Covers GET /api/v1/legal/terms (public version), POST /api/v1/legal/accept
(records consent for the current user), and the /register `accepted_terms`
flag that stamps consent at signup.
"""

import asyncio
import os

import pytest
from fastapi.testclient import TestClient

os.environ.setdefault("AUTH_JWT_SECRET", "test-secret-key-legal-consent-min-32char")

from app.gateway.auth.config import AuthConfig, set_auth_config
from app.gateway.legal import TOS_VERSION

_TEST_SECRET = "test-secret-key-legal-consent-min-32char"
_PASSWORD = "Tr0ub4dor3a"


@pytest.fixture(autouse=True)
def _setup_auth(tmp_path):
    from app.gateway import deps
    from deerflow.persistence.engine import close_engine, init_engine

    set_auth_config(AuthConfig(jwt_secret=_TEST_SECRET))
    url = f"sqlite+aiosqlite:///{tmp_path}/legal.db"
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


def _csrf(client: TestClient) -> dict[str, str]:
    token = client.cookies.get("csrf_token")
    return {"X-CSRF-Token": token} if token else {}


def test_terms_version_is_public(client):
    """GET /legal/terms returns the current versions without a session."""
    resp = client.get("/api/v1/legal/terms")
    assert resp.status_code == 200
    assert resp.json()["tos_version"] == TOS_VERSION


def test_register_with_consent_stamps_version(client):
    """Signup with accepted_terms=True records the current TOS version."""
    resp = client.post(
        "/api/v1/auth/register",
        json={"email": "consented@example.com", "password": _PASSWORD, "accepted_terms": True},
    )
    assert resp.status_code == 201, resp.text
    me = client.get("/api/v1/auth/me").json()
    assert me["tos_accepted_version"] == TOS_VERSION


def test_register_without_consent_leaves_version_null(client):
    """Signup without acknowledging terms leaves consent unset (gate will prompt)."""
    resp = client.post(
        "/api/v1/auth/register",
        json={"email": "noconsent@example.com", "password": _PASSWORD},
    )
    assert resp.status_code == 201, resp.text
    me = client.get("/api/v1/auth/me").json()
    assert me["tos_accepted_version"] is None


def test_accept_terms_records_consent(client):
    """POST /legal/accept stamps the current version onto the caller."""
    client.post("/api/v1/auth/register", json={"email": "later@example.com", "password": _PASSWORD})
    assert client.get("/api/v1/auth/me").json()["tos_accepted_version"] is None

    resp = client.post("/api/v1/legal/accept", headers=_csrf(client))
    assert resp.status_code == 200, resp.text
    assert resp.json()["accepted"] is True
    assert resp.json()["accepted_version"] == TOS_VERSION
    assert client.get("/api/v1/auth/me").json()["tos_accepted_version"] == TOS_VERSION


def test_accept_terms_requires_auth(client):
    """POST /legal/accept without a session → 401 (valid CSRF, no auth)."""
    client.post("/api/v1/auth/register", json={"email": "drop@example.com", "password": _PASSWORD})
    headers = _csrf(client)
    client.cookies.delete("access_token")

    resp = client.post("/api/v1/legal/accept", headers=headers)
    assert resp.status_code == 401
