"""Bring-your-own-key (BYOK) — encrypted per-user LLM API key storage.

Users can supply their own provider API key. Runs then bill to the user's
own account and bypass Nova's credit wall. Keys are encrypted at rest with
Fernet and never returned in plaintext.

Gated OFF by default. BYOK is active only when BOTH are true:
- ``NOVA_BYOK_ENABLED=1``
- ``NOVA_BYOK_SECRET`` is set to a valid Fernet key (``Fernet.generate_key()``)

This keeps the feature dark until an operator has provisioned a secret and
verified the run path, so it can never silently let a run bypass the wall
while still using Nova's key.
"""

from __future__ import annotations

import logging
import os

from cryptography.fernet import Fernet, InvalidToken
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from deerflow.persistence.engine import get_session_factory
from deerflow.persistence.user_api_key.model import UserApiKeyRow

logger = logging.getLogger(__name__)

BYOK_ENABLED_ENV = "NOVA_BYOK_ENABLED"
BYOK_SECRET_ENV = "NOVA_BYOK_SECRET"


def _secret() -> bytes | None:
    raw = os.environ.get(BYOK_SECRET_ENV, "").strip()
    return raw.encode() if raw else None


def _fernet() -> Fernet | None:
    """Build a Fernet from the configured secret, or None if invalid/unset."""
    secret = _secret()
    if not secret:
        return None
    try:
        return Fernet(secret)
    except (ValueError, TypeError):
        logger.warning("%s is set but is not a valid Fernet key; BYOK disabled", BYOK_SECRET_ENV)
        return None


def byok_enabled() -> bool:
    """True when the feature flag is on AND a valid encryption secret exists."""
    return os.environ.get(BYOK_ENABLED_ENV, "").strip() == "1" and _fernet() is not None


def encrypt_key(plaintext: str) -> str:
    fernet = _fernet()
    if fernet is None:
        raise RuntimeError("BYOK secret is not configured")
    return fernet.encrypt(plaintext.encode()).decode()


def decrypt_key(token: str) -> str | None:
    fernet = _fernet()
    if fernet is None:
        return None
    try:
        return fernet.decrypt(token.encode()).decode()
    except (InvalidToken, ValueError):
        logger.warning("Failed to decrypt a stored BYOK key (secret rotated?)")
        return None


async def set_user_key(
    user_id: str,
    provider: str,
    api_key: str,
    *,
    session_factory: async_sessionmaker[AsyncSession] | None = None,
) -> None:
    """Store (or replace) the user's encrypted API key."""
    from datetime import UTC, datetime

    sf = session_factory or get_session_factory()
    encrypted = encrypt_key(api_key)
    async with sf() as session:
        row = await session.get(UserApiKeyRow, str(user_id))
        now = datetime.now(UTC)
        if row is None:
            session.add(
                UserApiKeyRow(
                    user_id=str(user_id),
                    provider=provider,
                    encrypted_key=encrypted,
                    created_at=now,
                    updated_at=now,
                )
            )
        else:
            row.provider = provider
            row.encrypted_key = encrypted
            row.updated_at = now
        await session.commit()


async def clear_user_key(
    user_id: str,
    *,
    session_factory: async_sessionmaker[AsyncSession] | None = None,
) -> None:
    sf = session_factory or get_session_factory()
    async with sf() as session:
        await session.execute(delete(UserApiKeyRow).where(UserApiKeyRow.user_id == str(user_id)))
        await session.commit()


async def get_key_row(
    user_id: str,
    *,
    session_factory: async_sessionmaker[AsyncSession] | None = None,
) -> UserApiKeyRow | None:
    sf = session_factory or get_session_factory()
    async with sf() as session:
        stmt = select(UserApiKeyRow).where(UserApiKeyRow.user_id == str(user_id))
        return (await session.execute(stmt)).scalar_one_or_none()


async def has_active_key(
    user_id: str,
    *,
    session_factory: async_sessionmaker[AsyncSession] | None = None,
) -> bool:
    """True when BYOK is enabled and the user has a stored key."""
    if not byok_enabled():
        return False
    return await get_key_row(user_id, session_factory=session_factory) is not None


async def get_decrypted_key(
    user_id: str,
    *,
    session_factory: async_sessionmaker[AsyncSession] | None = None,
) -> str | None:
    """Return the user's decrypted key, or None when unavailable/disabled."""
    if not byok_enabled():
        return None
    row = await get_key_row(user_id, session_factory=session_factory)
    if row is None:
        return None
    return decrypt_key(row.encrypted_key)
