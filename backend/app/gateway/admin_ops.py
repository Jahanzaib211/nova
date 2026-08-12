"""Data-plane helpers for the ops console: audit trail + activity feed.

Kept out of the router so the SQL lives in one place and can be unit-tested
directly. All functions accept an optional ``session_factory`` (default: the
shared engine) and fail closed only on a genuinely missing engine.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import TYPE_CHECKING
from uuid import uuid4

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from deerflow.persistence.admin_audit.model import AdminAuditRow
from deerflow.persistence.engine import get_session_factory
from deerflow.persistence.run.model import RunRow
from deerflow.persistence.user.model import UserRow

if TYPE_CHECKING:
    from fastapi import Request


def utc(dt: datetime | None) -> datetime | None:
    """Normalize a stored datetime to tz-aware UTC for the wire.

    SQLite-backed ``DateTime(timezone=True)`` columns come back *naive*
    (SQLite has no timezone support), and Pydantic then serializes naive
    datetimes without an offset. Browsers parse an offset-less ISO string
    as **local** time, so every timestamp the console renders would drift
    by the viewer's UTC offset. Assume naive values are UTC (they always
    are: we only ever write ``datetime.now(UTC)``) and stamp them.
    """
    if dt is None:
        return None
    if dt.tzinfo is None:
        return dt.replace(tzinfo=UTC)
    return dt.astimezone(UTC)


def _request_actor_meta(request: "Request") -> tuple[str | None, str | None]:
    """Pull the IP/UA pair AuthMiddleware has stamped on request.state.

    Returns ``(None, None)`` when the request is not an HTTP request (e.g.
    a unit test calling ``record_audit`` directly), so the audit row
    gracefully skips the columns.
    """
    return (
        getattr(request.state, "actor_ip", None),
        getattr(request.state, "actor_user_agent", None),
    )


async def record_audit(
    *,
    actor: str,
    action: str,
    target_user_id: str | None,
    payload: dict,
    request: "Request | None" = None,
    session_factory: async_sessionmaker[AsyncSession] | None = None,
) -> None:
    """Append one operator action to the audit trail (best-effort).

    When ``request`` is supplied, the operator's IP and User-Agent are
    derived from ``request.state`` (stamped by AuthMiddleware) and written
    alongside the other columns. Calling sites that don't have a request
    (background jobs, CLIs) can omit it.
    """
    sf = session_factory or get_session_factory()
    if sf is None:
        return
    actor_ip, actor_user_agent = (None, None)
    if request is not None:
        actor_ip, actor_user_agent = _request_actor_meta(request)
    async with sf() as session:
        session.add(
            AdminAuditRow(
                id=uuid4().hex,
                actor=actor or "unknown",
                action=action,
                target_user_id=target_user_id,
                payload_json=payload or {},
                actor_ip=actor_ip,
                actor_user_agent=actor_user_agent,
                created_at=datetime.now(UTC),
            )
        )
        await session.commit()


async def list_audit(
    *,
    limit: int,
    offset: int,
    target_user_id: str | None = None,
    session_factory: async_sessionmaker[AsyncSession] | None = None,
) -> tuple[list[dict], int]:
    """Return (rows, total) of audit entries, newest first.

    ``target_user_id``, when given, restricts to rows recorded against that
    user (the audit trail's own ``target_user_id`` column, not the actor).
    """
    sf = session_factory or get_session_factory()
    if sf is None:
        return [], 0
    async with sf() as session:
        count_stmt = select(func.count()).select_from(AdminAuditRow)
        stmt = select(AdminAuditRow).order_by(AdminAuditRow.created_at.desc()).limit(limit).offset(offset)
        if target_user_id is not None:
            count_stmt = count_stmt.where(AdminAuditRow.target_user_id == target_user_id)
            stmt = stmt.where(AdminAuditRow.target_user_id == target_user_id)
        total = int(await session.scalar(count_stmt) or 0)
        rows = (await session.execute(stmt)).scalars().all()
        data = [
            {
                "id": r.id,
                "actor": r.actor,
                "action": r.action,
                "target_user_id": r.target_user_id,
                "payload": r.payload_json,
                "created_at": utc(r.created_at),
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
            "created_at": utc(created),
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


async def recent_users(
    *,
    limit: int,
    offset: int,
    session_factory: async_sessionmaker[AsyncSession] | None = None,
) -> list[dict]:
    """Newest users with their last-run + last-sign-in timestamps.

    Powers the Overview "Recent signups" panel and the per-user admin
    filters. Aggregation is one SQL query: ``runs`` is LEFT JOINed and
    aggregated with MAX(created_at), so the cost stays O(N) on the users
    table index rather than N+1 follow-up queries.

    The returned rows are dicts, not ORM objects, so the schema stays
    independent of the ``User`` pydantic shape — the live frontend
    field-by-field binds to the dict keys.
    """
    sf = session_factory or get_session_factory()
    if sf is None:
        return []
    from sqlalchemy import func as sql_func

    async with sf() as session:
        max_run = (
            select(RunRow.user_id, sql_func.max(RunRow.created_at).label("last_run_at"))
            .group_by(RunRow.user_id)
            .subquery()
        )
        stmt = (
            select(
                UserRow.id,
                UserRow.email,
                UserRow.system_role,
                UserRow.plan,
                UserRow.plan_status,
                UserRow.created_at,
                UserRow.last_sign_in_at,
                UserRow.is_forbidden,
                max_run.c.last_run_at,
            )
            .outerjoin(max_run, max_run.c.user_id == UserRow.id)
            .order_by(UserRow.created_at.desc())
            .limit(limit)
            .offset(offset)
        )
        result = await session.execute(stmt)
        rows = result.all()
        return [
            {
                "id": r.id,
                "email": r.email,
                "system_role": r.system_role,
                "plan": r.plan,
                "plan_status": r.plan_status,
                "created_at": utc(r.created_at),
                "last_sign_in_at": utc(r.last_sign_in_at),
                "last_run_at": utc(r.last_run_at),
                "is_forbidden": r.is_forbidden,
            }
            for r in rows
        ]


async def user_auth_history(
    *,
    user_id: str,
    limit: int = 50,
    session_factory: async_sessionmaker[AsyncSession] | None = None,
) -> list[dict]:
    """Audited auth events for a single user, newest first.

    Joins the audit trail on actions that target (or originate from) the
    user: ``login``, ``change-password``, ``update-email``, ``failed-login``,
    ``disable-all-sessions`` (admin revoke), ``reset-password`` (admin),
    and any explicitly-targeted admin action. The ``target_user_id``
    filter is exact; events without the user as actor or target are
    intentionally excluded.
    """
    sf = session_factory or get_session_factory()
    if sf is None:
        return []
    async with sf() as session:
        stmt = (
            select(AdminAuditRow)
            .where(
                (AdminAuditRow.target_user_id == user_id)
                | (AdminAuditRow.actor == (select(UserRow.email).where(UserRow.id == user_id).scalar_subquery()))
            )
            .order_by(AdminAuditRow.created_at.desc())
            .limit(limit)
        )
        result = await session.execute(stmt)
        rows = result.scalars().all()
        return [
            {
                "id": r.id,
                "actor": r.actor,
                "action": r.action,
                "target_user_id": r.target_user_id,
                "payload": r.payload_json,
                "actor_ip": r.actor_ip,
                "actor_user_agent": r.actor_user_agent,
                "created_at": utc(r.created_at),
            }
            for r in rows
        ]
