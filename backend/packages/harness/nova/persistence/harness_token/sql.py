"""Harness tokens: bearer credentials for external harnesses.

``nhk_`` + 43 url-safe base64 characters (256 bits). Shown once; stored as
SHA-256. Scoped to capability modules; revocation is a timestamp on the row
so it takes effect on the next request.
"""

from __future__ import annotations

import hashlib
import re
import secrets
import uuid
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from deerflow.persistence.harness_token.model import HarnessTokenRow

TOKEN_PREFIX = "nhk_"
_SHAPE = re.compile(r"^nhk_[A-Za-z0-9_-]{43}$")


def is_token_shaped(value: str | None) -> bool:
    return bool(value) and bool(_SHAPE.match(value or ""))


def hash_token(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


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
    }


class HarnessTokenRepository:
    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        self._sf = session_factory

    async def create(self, *, owner_user_id: str, name: str, scopes: list[str]) -> dict[str, Any]:
        token = TOKEN_PREFIX + secrets.token_urlsafe(32)
        row = HarnessTokenRow(id=str(uuid.uuid4()), owner_user_id=owner_user_id, name=name[:128], token_hash=hash_token(token), prefix=token[:12], scopes_json=sorted(set(scopes)) or ["*"])
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
            if row is None or row.revoked_at is not None:
                return None
            row.last_used_at = datetime.now(UTC)
            await session.commit()
            return _row(row)

    async def revoke(self, token_id: str, *, owner_user_id: str) -> bool:
        async with self._sf() as session:
            res = await session.execute(update(HarnessTokenRow).where(HarnessTokenRow.id == token_id, HarnessTokenRow.owner_user_id == owner_user_id, HarnessTokenRow.revoked_at.is_(None)).values(revoked_at=datetime.now(UTC)))
            await session.commit()
            return (res.rowcount or 0) > 0
