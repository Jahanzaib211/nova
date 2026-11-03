"""Harness tokens: bearer credentials for external harnesses.

``nhk_`` + 43 url-safe base64 characters (256 bits). Shown once; stored as
SHA-256. Scoped to capability modules; revocation is a timestamp on the row
so it takes effect on the next request.

Per-turn runtime/delegate tokens (named ``runtime:<thread>``) also carry an
``expires_at``: the session revokes them when it closes, but a revoke that
fails must not leave a bearer token for the user's sandbox live forever.
:meth:`HarnessTokenRepository.revoke_stale_runtime_tokens` is the startup
sweep for the ones that slipped through.
"""

from __future__ import annotations

import hashlib
import re
import secrets
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from nova.persistence.harness_token.model import HarnessTokenRow

TOKEN_PREFIX = "nhk_"
#: Name prefix of per-turn tokens minted by an ACP session.
RUNTIME_TOKEN_NAME_PREFIX = "runtime:"
_SHAPE = re.compile(r"^nhk_[A-Za-z0-9_-]{43}$")


def is_token_shaped(value: str | None) -> bool:
    return bool(value) and bool(_SHAPE.match(value or ""))


def hash_token(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def _aware(value: datetime | None) -> datetime | None:
    """SQLite hands back naive datetimes for ``DateTime(timezone=True)``; they are UTC."""
    if value is not None and value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value


def is_expired(expires_at: datetime | None, *, now: datetime | None = None) -> bool:
    expires_at = _aware(expires_at)
    return expires_at is not None and expires_at <= (now or datetime.now(UTC))


def _row(r: HarnessTokenRow) -> dict[str, Any]:
    return {
        "id": r.id,
        "owner_user_id": r.owner_user_id,
        "name": r.name,
        "prefix": r.prefix,
        "scopes": list(r.scopes_json or []),
        "created_at": r.created_at.isoformat() if r.created_at else None,
        "last_used_at": r.last_used_at.isoformat() if r.last_used_at else None,
        "revoked_at": r.revoked_at.isoformat() if r.revoked_at else None,
        "expires_at": _aware(r.expires_at).isoformat() if r.expires_at else None,
    }


class HarnessTokenRepository:
    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        self._sf = session_factory

    async def create(self, *, owner_user_id: str, name: str, scopes: list[str], ttl_seconds: float | None = None) -> dict[str, Any]:
        """Mint a token. ``ttl_seconds`` sets a hard expiry; None means none."""
        token = TOKEN_PREFIX + secrets.token_urlsafe(32)
        expires_at = datetime.now(UTC) + timedelta(seconds=ttl_seconds) if ttl_seconds else None
        row = HarnessTokenRow(
            id=str(uuid.uuid4()),
            owner_user_id=owner_user_id,
            name=name[:128],
            token_hash=hash_token(token),
            prefix=token[:12],
            scopes_json=sorted(set(scopes)) or ["*"],
            expires_at=expires_at,
        )
        async with self._sf() as session:
            session.add(row)
            await session.commit()
            await session.refresh(row)
            out = _row(row)
        out["token"] = token
        return out

    async def list(self, *, owner_user_id: str) -> list[dict[str, Any]]:
        async with self._sf() as session:
            rows = (await session.execute(select(HarnessTokenRow).where(HarnessTokenRow.owner_user_id == owner_user_id).order_by(HarnessTokenRow.created_at.desc()))).scalars().all()
            return [_row(r) for r in rows]

    async def resolve(self, token: str | None) -> dict[str, Any] | None:
        """Owner + scopes for a live token, touching ``last_used_at``; None otherwise."""
        if not is_token_shaped(token):
            return None
        digest = hash_token(token)  # type: ignore[arg-type]
        async with self._sf() as session:
            row = (await session.execute(select(HarnessTokenRow).where(HarnessTokenRow.token_hash == digest))).scalar_one_or_none()
            if row is None or row.revoked_at is not None or is_expired(row.expires_at):
                return None
            row.last_used_at = datetime.now(UTC)
            await session.commit()
            return _row(row)

    async def revoke(self, token_id: str, *, owner_user_id: str) -> bool:
        async with self._sf() as session:
            res = await session.execute(update(HarnessTokenRow).where(HarnessTokenRow.id == token_id, HarnessTokenRow.owner_user_id == owner_user_id, HarnessTokenRow.revoked_at.is_(None)).values(revoked_at=datetime.now(UTC)))
            await session.commit()
            return (res.rowcount or 0) > 0

    async def revoke_stale_runtime_tokens(self, *, max_age_seconds: float, now: datetime | None = None) -> int:
        """Revoke per-turn runtime tokens that can no longer belong to a live session.

        Stale means expired, or -- for rows minted before ``expires_at``
        existed -- created more than ``max_age_seconds`` ago. A runtime token
        younger than that may belong to a session still running in another
        process (the jobs worker runs delegated ACP turns), so it is left
        alone; its own expiry or the next sweep handles it. Human-minted
        tokens (any other name) are never touched. Returns rows revoked.
        """
        now = now or datetime.now(UTC)
        cutoff = now - timedelta(seconds=max_age_seconds)
        async with self._sf() as session:
            rows = (
                (
                    await session.execute(
                        select(HarnessTokenRow).where(
                            HarnessTokenRow.name.like(f"{RUNTIME_TOKEN_NAME_PREFIX}%"),
                            HarnessTokenRow.revoked_at.is_(None),
                        )
                    )
                )
                .scalars()
                .all()
            )
            stale = 0
            for row in rows:
                created = _aware(row.created_at)
                if is_expired(row.expires_at, now=now) or (row.expires_at is None and created is not None and created <= cutoff):
                    row.revoked_at = now
                    stale += 1
            if stale:
                await session.commit()
            return stale
