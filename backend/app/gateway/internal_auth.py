"""Authentication for trusted Gateway internal callers."""

from __future__ import annotations

import logging
import os
import secrets
from types import SimpleNamespace
from typing import Any

from app.gateway.auth_disabled import is_explicit_production_environment
from deerflow.runtime.user_context import DEFAULT_USER_ID

logger = logging.getLogger(__name__)

INTERNAL_AUTH_HEADER_NAME = "X-DeerFlow-Internal-Token"
INTERNAL_OWNER_USER_ID_HEADER_NAME = "X-DeerFlow-Owner-User-Id"
INTERNAL_AUTH_ENV_VAR = "DEER_FLOW_INTERNAL_AUTH_TOKEN"
INTERNAL_SYSTEM_ROLE = "internal"


def _load_internal_auth_token() -> str:
    token = os.environ.get(INTERNAL_AUTH_ENV_VAR, "").strip()
    if token:
        return token

    # Auth-disabled mode is a documented opt-out for local dev / E2E: the
    # internal channel workers run alongside the gateway in the same process
    # and have no way to share a per-process secret, so we synthesise one
    # here. The token still authenticates internal callers within the
    # process; it just isn't externally exposed.
    if is_explicit_production_environment():
        raise RuntimeError(
            f"{INTERNAL_AUTH_ENV_VAR} is required in production but is unset. Refusing to start: an auto-generated token would grant full admin permissions to any caller able to read this process's environment.",
        )
    logger.warning(
        "%s is unset; auto-generating a per-process random token. Internal channel workers in this process will authenticate, but external callers cannot. Set the env var in non-prod too for multi-worker / cross-process deployments.",
        INTERNAL_AUTH_ENV_VAR,
    )
    return secrets.token_urlsafe(32)


_INTERNAL_AUTH_TOKEN = _load_internal_auth_token()


def create_internal_auth_headers(*, owner_user_id: str | None = None) -> dict[str, str]:
    """Return headers that authenticate trusted Gateway internal calls."""
    headers = {INTERNAL_AUTH_HEADER_NAME: _INTERNAL_AUTH_TOKEN}
    if owner_user_id:
        headers[INTERNAL_OWNER_USER_ID_HEADER_NAME] = owner_user_id
    return headers


def is_valid_internal_auth_token(token: str | None) -> bool:
    """Return True when *token* matches this Gateway worker's internal token."""
    return bool(token) and secrets.compare_digest(token, _INTERNAL_AUTH_TOKEN)


def get_internal_user():
    """Return the synthetic user used for trusted internal channel calls."""
    return SimpleNamespace(id=DEFAULT_USER_ID, system_role=INTERNAL_SYSTEM_ROLE)


def get_trusted_internal_owner_user_id(request: Any) -> str | None:
    """Return the owner override for a trusted internal request, if present.

    The header is ignored for normal browser/API callers. It is only honored
    after ``AuthMiddleware`` has validated the internal auth token and stamped
    the synthetic internal user onto ``request.state.user``.
    """
    user = getattr(getattr(request, "state", None), "user", None)
    if getattr(user, "system_role", None) != INTERNAL_SYSTEM_ROLE:
        return None

    owner_user_id = request.headers.get(INTERNAL_OWNER_USER_ID_HEADER_NAME)
    if not owner_user_id:
        return None
    owner_user_id = owner_user_id.strip()
    return owner_user_id or None
