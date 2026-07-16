"""Service-token authentication for the Ali Technologies ops console.

The ops console (a separate app) is the ONLY caller that presents this
token. It authenticates as a synthetic **admin** principal so it can reach
the ``require_admin_user``-gated ``/api/v1/admin/*`` endpoints without
holding a real user's password. Mirrors ``internal_auth.py``:

- Token comes from ``NOVA_OPS_TOKEN`` (env). When UNSET, ops auth is fully
  disabled — no token can authenticate, and there is no auto-generated
  fallback (unlike internal_auth, which needs one for in-process channel
  workers). This keeps the god-mode surface dark until an operator opts in.
- Constant-time comparison; the console sends it only server-side (BFF),
  never to a browser.
"""

from __future__ import annotations

import logging
import os
import secrets
from types import SimpleNamespace

logger = logging.getLogger(__name__)

OPS_AUTH_HEADER_NAME = "X-Nova-Ops-Token"
OPS_AUTH_ENV_VAR = "NOVA_OPS_TOKEN"

# Synthetic principal id for the console. system_role="admin" so it clears
# require_admin_user; the id is used as the audit-log actor fallback.
OPS_CONSOLE_USER_ID = "ops-console"


def _ops_token() -> str | None:
    token = os.environ.get(OPS_AUTH_ENV_VAR, "").strip()
    return token or None


def ops_auth_enabled() -> bool:
    """True when an ops service token is configured."""
    return _ops_token() is not None


def is_valid_ops_token(token: str | None) -> bool:
    """Return True when *token* matches the configured ops token.

    Always False when ops auth is disabled (no token configured), so an
    absent env var can never be bypassed by sending an empty header.
    """
    configured = _ops_token()
    if configured is None or not token:
        return False
    return secrets.compare_digest(token, configured)


def get_ops_user():
    """Return the synthetic admin principal for authenticated ops calls."""
    return SimpleNamespace(id=OPS_CONSOLE_USER_ID, email="ops-console", system_role="admin")
