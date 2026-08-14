"""Password-reset token lifecycle for the self-service forgot-password flow.

Tokens are unguessable (``secrets.token_urlsafe(32)``), stored only as
SHA-256 digests, single-use, and valid for 30 minutes. Requesting a new
token for the same user invalidates any previous unconsumed token; the
last request wins.
"""

from __future__ import annotations

import hashlib
import secrets
from datetime import UTC, datetime, timedelta

from sqlalchemy import delete, select

from deerflow.persistence.password_reset_token.model import PasswordResetTokenRow

RESET_TOKEN_TTL = timedelta(minutes=30)


def _hash_token(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def _now() -> datetime:
    return datetime.now(UTC)


async def create_reset_token(sf, *, user_id: str) -> str:
    """Create a fresh reset token for the user, invalidating any previous one.

    Returns the **raw** token — the caller embeds it in the reset email;
    it is not stored anywhere in plaintext.
    """
    token = secrets.token_urlsafe(32)

    async with sf() as session:
        await session.execute(
            delete(PasswordResetTokenRow).where(
                PasswordResetTokenRow.user_id == str(user_id),
                PasswordResetTokenRow.consumed.is_(False),
            )
        )
        session.add(
            PasswordResetTokenRow(
                token_hash=_hash_token(token),
                user_id=str(user_id),
                created_at=_now(),
                expires_at=_now() + RESET_TOKEN_TTL,
                consumed=False,
            )
        )
        await session.commit()

    return token


async def consume_token(sf, *, token: str) -> str | None:
    """Atomically validate and consume a reset token.

    Returns the owning user id when the token is valid (known, unexpired,
    unconsumed) — the token is marked consumed in the same transaction so
    a replay of the same reset link is rejected. Returns ``None``
    otherwise.
    """
    token_hash = _hash_token(token)

    async with sf() as session:
        row = (await session.execute(select(PasswordResetTokenRow).where(PasswordResetTokenRow.token_hash == token_hash))).scalar_one_or_none()
        if row is None:
            return None
        if row.consumed:
            return None

        expires_at = row.expires_at if row.expires_at.tzinfo else row.expires_at.replace(tzinfo=UTC)
        if expires_at < _now():
            return None

        user_id = row.user_id
        row.consumed = True
        row.consumed_at = _now()
        await session.commit()
        return user_id
