"""Data-plane helpers for the ops console: audit trail + activity feed.

Kept out of the router so the SQL lives in one place and can be unit-tested
directly. All functions accept an optional ``session_factory`` (default: the
shared engine) and fail closed only on a genuinely missing engine.
"""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import uuid4

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from deerflow.persistence.admin_audit.model import AdminAuditRow
from deerflow.persistence.engine import get_session_factory
from deerflow.persistence.run.model import RunRow
from deerflow.persistence.user.model import UserRow


async def record_audit(
    *,
    actor: str,
    action: str,
    target_user_id: str | None,
    payload: dict,
    session_factory: async_sessionmaker[AsyncSession] | None = None,
) -> None:
    """Append one operator action to the audit trail (best-effort)."""
    sf = session_factory or get_session_factory()
    if sf is None:
        return
    async with sf() as session:
        session.add(
            AdminAuditRow(
                id=uuid4().hex,
                actor=actor or "unknown",
                action=action,
                target_user_id=target_user_id,
                payload_json=payload or {},
                created_at=datetime.now(UTC),
            )
        )
        await session.commit()


async def list_audit(
    *,
    limit: int,
    offset: int,
    session_factory: async_sessionmaker[AsyncSession] | None = None,
) -> tuple[list[dict], int]:
    """Return (rows, total) of audit entries, newest first."""
    sf = session_factory or get_session_factory()
    if sf is None:
        return [], 0
    async with sf() as session:
        total = int(await session.scalar(select(func.count()).select_from(AdminAuditRow)) or 0)
        stmt = select(AdminAuditRow).order_by(AdminAuditRow.created_at.desc()).limit(limit).offset(offset)
        rows = (await session.execute(stmt)).scalars().all()
        data = [
            {
                "id": r.id,
                "actor": r.actor,
                "action": r.action,
                "target_user_id": r.target_user_id,
                "payload": r.payload_json,
                "created_at": r.created_at,
            }
            for r in rows
        ]
        return data, total


async def _runs_query(session, *, user_id: str | None, limit: int, offset: int):
    stmt = (
        select(
            RunRow.run_id,
            RunRow.user_id,
            UserRow.email,
            RunRow.total_tokens,
            RunRow.model_name,
            RunRow.status,
            RunRow.created_at,
        )
        .outerjoin(UserRow, RunRow.user_id == UserRow.id)
        .order_by(RunRow.created_at.desc())
        .limit(limit)
        .offset(offset)
    )
    if user_id is not None:
        stmt = stmt.where(RunRow.user_id == user_id)
    result = await session.execute(stmt)
    return [
        {
            "run_id": run_id,
            "user_id": uid,
            "email": email,
            "total_tokens": tokens or 0,
            "model_name": model,
            "status": status_,
            "created_at": created,
        }
        for (run_id, uid, email, tokens, model, status_, created) in result.all()
    ]


async def recent_activity(
    *,
    limit: int,
    offset: int,
    session_factory: async_sessionmaker[AsyncSession] | None = None,
) -> list[dict]:
    """Recent runs across all users (with the owner's email), newest first."""
    sf = session_factory or get_session_factory()
    if sf is None:
        return []
    async with sf() as session:
        return await _runs_query(session, user_id=None, limit=limit, offset=offset)


async def recent_runs_for_user(
    user_id: str,
    *,
    limit: int = 10,
    session_factory: async_sessionmaker[AsyncSession] | None = None,
) -> list[dict]:
    """Recent runs for a single user, newest first."""
    sf = session_factory or get_session_factory()
    if sf is None:
        return []
    async with sf() as session:
        return await _runs_query(session, user_id=user_id, limit=limit, offset=0)
