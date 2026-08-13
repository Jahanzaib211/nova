"""Data-plane helpers for the ops console: audit trail + activity feed.

Kept out of the router so the SQL lives in one place and can be unit-tested
directly. All functions accept an optional ``session_factory`` (default: the
shared engine) and fail closed only on a genuinely missing engine.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
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


async def studio_users(
    *,
    sort: str = "tokens",
    limit: int = 25,
    offset: int = 0,
    plan: str | None = None,
    session_factory: async_sessionmaker[AsyncSession] | None = None,
) -> dict:
    """Ranked "users studio" view for the Overview's whole-of-user-base ranking.

    The existing ``/users`` returns the bare roster; ``/users/recent``
    returns newest signups; ``/users/stats`` returns aggregates. None of
    them ranks the whole user base by a chosen metric. The studio is
    the view that asks "who is generating the action on this system",
    so it joins the user table with two aggregations:

    1. ``runs`` — COUNT + SUM(total_tokens) per user. Powers the "By
       tokens" sort and the bar visual on every row.
    2. ``admin_audit`` filtered by ``action='failed-login'`` and the
       last 7 days — powers the dormancy-failure signal on the
       "Recency" sort.

    A single SQL query does the work: the two aggregations are
    sub-queries, the main ``SELECT`` joins ``users`` to both, filters
    by plan (when supplied), and orders by the chosen column. The
    total row count is exposed separately so the page can render a
    paginator without a second query.

    Returns a dict with:
      - ``ranking``: list of dicts, one per user, sorted by the chosen key
      - ``by_plan``: aggregate count per plan (free / plus / enterprise)
      - ``metrics``: dormant_count, forbidden_count, no_run_count,
        active_30d, total_users (each rendered as a KPI on the page)
      - ``total_users``, ``limit``, ``offset``, ``sort``
    """
    sf = session_factory or get_session_factory()
    if sf is None:
        return {
            "ranking": [],
            "by_plan": {},
            "metrics": {
                "dormant_count": 0,
                "forbidden_count": 0,
                "no_run_count": 0,
                "active_30d": 0,
                "total_users": 0,
            },
            "total_users": 0,
            "limit": limit,
            "offset": offset,
            "sort": sort,
        }
    from sqlalchemy import func as sql_func

    async with sf() as session:
        # Per-user run aggregates.
        run_agg = (
            select(
                RunRow.user_id.label("user_id"),
                sql_func.count(RunRow.run_id).label("run_count"),
                sql_func.coalesce(sql_func.sum(RunRow.total_tokens), 0).label("lifetime_tokens"),
            )
            .group_by(RunRow.user_id)
            .subquery()
        )

        # Failed-login count in the last 7 days per user (actor email matched).
        # We aggregate by actor (email) for failed-login events and later
        # LEFT JOIN to users by email. The asymmetry is intentional: failed-login
        # rows are written with the submitted email in the actor column.
        last_7d = datetime.now(UTC) - timedelta(days=7)
        failed_agg = (
            select(
                AdminAuditRow.actor.label("actor"),
                sql_func.count(AdminAuditRow.id).label("failed_count"),
            )
            .where(
                AdminAuditRow.action == "failed-login",
                AdminAuditRow.created_at >= last_7d,
            )
            .group_by(AdminAuditRow.actor)
            .subquery()
        )

        # Main ranking query.
        order_col = {
            "tokens": run_agg.c.lifetime_tokens.desc().nulls_last(),
            "activity": UserRow.last_sign_in_at.desc().nulls_last(),
            "runs": run_agg.c.run_count.desc().nulls_last(),
            "recency": UserRow.created_at.desc(),
            "failed": failed_agg.c.failed_count.desc().nulls_last(),
        }.get(sort, run_agg.c.lifetime_tokens.desc().nulls_last())

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
                run_agg.c.run_count,
                run_agg.c.lifetime_tokens,
                failed_agg.c.failed_count,
            )
            .outerjoin(run_agg, run_agg.c.user_id == UserRow.id)
            .outerjoin(failed_agg, failed_agg.c.actor == UserRow.email)
        )
        if plan and plan in ("free", "plus", "enterprise"):
            stmt = stmt.where(UserRow.plan == plan)
        stmt = stmt.order_by(order_col).limit(limit).offset(offset)
        result = await session.execute(stmt)
        rows = result.all()

        # Aggregate metrics — separate small queries so the page can
        # render KPI strips above the ranking table.
        total_users = await session.scalar(select(sql_func.count(UserRow.id))) or 0
        forbidden_count = await session.scalar(
            select(sql_func.count(UserRow.id)).where(UserRow.is_forbidden == True)  # noqa: E712
        ) or 0
        active_30d = await session.scalar(
            select(sql_func.count(UserRow.id)).where(
                UserRow.last_sign_in_at >= (datetime.now(UTC) - timedelta(days=30))
            )
        ) or 0
        dormant_count = await session.scalar(
            select(sql_func.count(UserRow.id)).where(
                UserRow.last_sign_in_at < (datetime.now(UTC) - timedelta(days=7))
            )
        ) or 0
        no_run_count = await session.scalar(
            select(sql_func.count(UserRow.id))
            .select_from(UserRow)
            .outerjoin(run_agg, run_agg.c.user_id == UserRow.id)
            .where(run_agg.c.user_id.is_(None))
        ) or 0
        by_plan_rows = (
            await session.execute(
                select(UserRow.plan, sql_func.count(UserRow.id)).group_by(UserRow.plan)
            )
        ).all()
        by_plan = {plan: count for plan, count in by_plan_rows}

        return {
            "ranking": [
                {
                    "id": r.id,
                    "email": r.email,
                    "system_role": r.system_role,
                    "plan": r.plan,
                    "plan_status": r.plan_status,
                    "created_at": utc(r.created_at),
                    "last_sign_in_at": utc(r.last_sign_in_at),
                    "is_forbidden": r.is_forbidden,
                    "run_count": int(r.run_count or 0),
                    "lifetime_tokens": int(r.lifetime_tokens or 0),
                    "recent_failed_login_count": int(r.failed_count or 0),
                }
                for r in rows
            ],
            "by_plan": by_plan,
            "metrics": {
                "dormant_count": int(dormant_count),
                "forbidden_count": int(forbidden_count),
                "no_run_count": int(no_run_count),
                "active_30d": int(active_30d),
                "total_users": int(total_users),
            },
            "total_users": int(total_users),
            "limit": limit,
            "offset": offset,
            "sort": sort,
        }


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
