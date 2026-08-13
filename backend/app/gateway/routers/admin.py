"""Admin-only endpoints: registration visibility, user administration, and
the ops-console god-mode controls (usage reset, credit grants, custom
limits, plan overrides) — every mutation written to the audit trail.

Gated by :func:`require_admin_user`. Reachable by a real admin session or by
the ops console's service token (see ``ops_auth.py``).
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Literal

from fastapi import APIRouter, HTTPException, Query, Request, Response, status
from pydantic import BaseModel, Field, field_serializer

from app.gateway import admin_ops
from app.gateway.credits import get_balance
from app.gateway.deps import get_local_provider, get_run_service, require_admin_user
from app.gateway.referrals import _add_grant
from app.gateway.routers.channel_connections import ChannelConnectionResponse, ChannelConnectionsResponse, _get_channel_connections_config, _get_repository
from app.gateway.routers.thread_runs import _cancel_conflict_detail
from deerflow.persistence.engine import get_session_factory
from deerflow.persistence.thread_meta import ThreadMetaRepository
from deerflow.runtime.events.store.db import DbRunEventStore
from deerflow.utils.time import coerce_iso

_NO_SQL_BACKEND_DETAIL = "Conversation history requires a SQL-backed deployment (config.yaml: database.backend=sqlite|postgres)."

router = APIRouter(prefix="/api/v1/admin", tags=["admin"])

_ADMIN_REQUIRED_DETAIL = "Admin privileges are required for this operation."


def _actor(request: Request) -> str:
    """Audit actor: the admin's email, or 'ops-console' for token calls."""
    user = getattr(request.state, "user", None)
    return getattr(user, "email", None) or getattr(user, "id", None) or "unknown"


class AdminUserRow(BaseModel):
    """One row in the admin users roster."""

    id: str
    email: str
    system_role: str
    plan: str
    plan_status: str | None = None
    created_at: datetime

    @field_serializer("created_at")
    def _ser_created_at(self, value: datetime) -> datetime:
        return admin_ops.utc(value)


class AdminUsersResponse(BaseModel):
    """Paginated users roster."""

    data: list[AdminUserRow]
    total: int
    limit: int
    offset: int
    has_more: bool


class AdminUserStats(BaseModel):
    """Aggregate registration metrics for the admin dashboard."""

    total: int
    by_plan: dict[str, int]
    new_last_7_days: int
    new_last_30_days: int


@router.get("/users", response_model=AdminUsersResponse)
async def list_users(
    request: Request,
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
) -> AdminUsersResponse:
    """List registered users, newest first. Admin only."""
    await require_admin_user(request, detail=_ADMIN_REQUIRED_DETAIL)

    provider = get_local_provider()
    total = await provider.count_users()
    users = await provider.list_users(limit=limit, offset=offset)

    await admin_ops.record_audit(
        actor=_actor(request),
        action="view-users",
        target_user_id=None,
        payload={"limit": limit, "offset": offset, "total": total},
        request=request,
    )

    rows = [
        AdminUserRow(
            id=str(u.id),
            email=u.email,
            system_role=u.system_role,
            plan=u.plan,
            plan_status=u.plan_status,
            created_at=u.created_at,
        )
        for u in users
    ]
    return AdminUsersResponse(
        data=rows,
        total=total,
        limit=limit,
        offset=offset,
        has_more=offset + len(rows) < total,
    )


# ── Recent users (per-user live stats) ──────────────────────────────────
# NOTE: this route is declared BEFORE the wildcard ``/users/{user_id}``
# route below — FastAPI matches routes in registration order, so a
# literal segment like "recent" must win over the parametric segment.

class RecentUserRow(BaseModel):
    """One row in the recent-users roster.

    Same shape as :class:`AdminUserRow` plus the per-user activity fields
    the Overview "Recent signups" panel needs: last successful login and
    last run timestamp (aggregated from the runs table).
    """

    id: str
    email: str
    system_role: str
    plan: str
    plan_status: str | None = None
    created_at: datetime
    last_sign_in_at: datetime | None = None
    last_run_at: datetime | None = None
    is_forbidden: bool = False

    @field_serializer("created_at", "last_sign_in_at", "last_run_at")
    def _ser(self, value: datetime | None) -> datetime | None:
        return admin_ops.utc(value)


class RecentUsersResponse(BaseModel):
    data: list[RecentUserRow]
    limit: int
    offset: int


@router.get("/users/recent", response_model=RecentUsersResponse)
async def recent_users(
    request: Request,
    limit: int = Query(20, ge=1, le=200),
    offset: int = Query(0, ge=0),
) -> RecentUsersResponse:
    """Per-user live stats: newest users with last-sign-in + last-run.

    The new ``last_sign_in_at`` column is stamped on every successful
    login (``POST /api/v1/auth/login/local``); ``last_run_at`` is
    aggregated from the ``runs`` table. The Overview's "Recent signups"
    panel polls this endpoint to surface per-user activity, and the
    user-detail page uses the same data to show "last run" against the
    admin's chosen user.

    Admin only.
    """
    await require_admin_user(request, detail=_ADMIN_REQUIRED_DETAIL)
    rows = await admin_ops.recent_users(limit=limit, offset=offset)
    await admin_ops.record_audit(
        actor=_actor(request),
        action="view-users-recent",
        target_user_id=None,
        payload={"limit": limit, "offset": offset, "rows": len(rows)},
        request=request,
    )
    return RecentUsersResponse(
        data=[RecentUserRow(**row) for row in rows],
        limit=limit,
        offset=offset,
    )


# ── Users studio (ranked view of the whole user base) ──────────────────


class StudioUserEntry(BaseModel):
    """One row in the ranked users-studio table.

    Composition of every per-user field the new schema exposes so the
    panel can sort, filter, and render the bar column without a follow-up
    request. The bar column is rendered client-side: the value is
    normalized to the heaviest user in the current page so the bar
    reflects the batch, not the global max.
    """

    id: str
    email: str
    system_role: str
    plan: str
    plan_status: str | None = None
    created_at: datetime
    last_sign_in_at: datetime | None = None
    is_forbidden: bool = False
    run_count: int = 0
    lifetime_tokens: int = 0
    recent_failed_login_count: int = 0

    @field_serializer("created_at", "last_sign_in_at")
    def _ser(self, value: datetime | None) -> datetime | None:
        return admin_ops.utc(value)


class StudioMetrics(BaseModel):
    dormant_count: int
    forbidden_count: int
    no_run_count: int
    active_30d: int
    total_users: int


class StudioResponse(BaseModel):
    ranking: list[StudioUserEntry]
    by_plan: dict[str, int]
    metrics: StudioMetrics
    total_users: int
    limit: int
    offset: int
    sort: str


@router.get("/users/studio", response_model=StudioResponse)
async def users_studio(
    request: Request,
    sort: str = Query("tokens", pattern="^(tokens|activity|runs|recency|failed)$"),
    plan: str | None = Query(None, pattern="^(free|plus|enterprise)$"),
    limit: int = Query(25, ge=1, le=200),
    offset: int = Query(0, ge=0),
) -> StudioResponse:
    """Ranked view of the whole user base.

    Powers the ``/users/studio`` page in the operator console. The
    existing roster endpoints return the bare list, recent signups, or
    aggregate stats — none of them rank the whole user base by a
    chosen metric. The studio joins ``users`` with two sub-aggregations
    (runs and recent failed-logins) so each row carries the data the
    ranked table needs without a follow-up request.

    Default sort is ``tokens`` (lifetime, descending) because the
    current dataset immediately surfaces the system-straining user
    (300+ runs, 200M+ tokens) instead of a chrono-only order.

    Admin only. Audited ``view-users-studio``.
    """
    await require_admin_user(request, detail=_ADMIN_REQUIRED_DETAIL)
    body = await admin_ops.studio_users(sort=sort, limit=limit, offset=offset, plan=plan)
    await admin_ops.record_audit(
        actor=_actor(request),
        action="view-users-studio",
        target_user_id=None,
        payload={"sort": sort, "limit": limit, "offset": offset, "plan": plan, "rows": len(body["ranking"])},
        request=request,
    )
    return StudioResponse(
        ranking=[StudioUserEntry(**row) for row in body["ranking"]],
        by_plan=body["by_plan"],
        metrics=StudioMetrics(**body["metrics"]),
        total_users=body["total_users"],
        limit=body["limit"],
        offset=body["offset"],
        sort=body["sort"],
    )


@router.get("/users/stats", response_model=AdminUserStats)
async def user_stats(request: Request) -> AdminUserStats:
    """Aggregate signup metrics: total, plan breakdown, recent growth. Admin only."""
    await require_admin_user(request, detail=_ADMIN_REQUIRED_DETAIL)

    provider = get_local_provider()
    now = datetime.now(UTC)
    total = await provider.count_users()
    by_plan = await provider.count_users_by_plan()
    new_7 = await provider.count_users_since(now - timedelta(days=7))
    new_30 = await provider.count_users_since(now - timedelta(days=30))

    await admin_ops.record_audit(
        actor=_actor(request),
        action="view-user-stats",
        target_user_id=None,
        payload={"total": total},
        request=request,
    )

    return AdminUserStats(
        total=total,
        by_plan=by_plan,
        new_last_7_days=new_7,
        new_last_30_days=new_30,
    )


class SetPlanRequest(BaseModel):
    """Admin request to set a user's plan manually (comp / enterprise)."""

    plan: str


@router.patch("/users/{user_id}/plan", response_model=AdminUserRow)
async def set_user_plan(user_id: str, body: SetPlanRequest, request: Request) -> AdminUserRow:
    """Manually set a user's plan. Admin only.

    Complements Stripe billing for comped, enterprise, or manually-granted
    accounts. Setting a plan here does not create a Stripe subscription.
    """
    await require_admin_user(request, detail=_ADMIN_REQUIRED_DETAIL)

    if body.plan not in ("free", "plus", "enterprise"):
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid plan")

    provider = get_local_provider()
    user = await provider.get_user(user_id)
    if user is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="User not found")

    user.plan = body.plan
    # Manual grant: reflect an admin-set status so the UI can distinguish it.
    user.plan_status = "admin_granted" if body.plan != "free" else None
    await provider.update_user(user)
    await admin_ops.record_audit(actor=_actor(request), action="set-plan", target_user_id=user_id, payload={"plan": body.plan}, request=request)

    return AdminUserRow(
        id=str(user.id),
        email=user.email,
        system_role=user.system_role,
        plan=user.plan,
        plan_status=user.plan_status,
        created_at=user.created_at,
    )


# ── God-mode controls (ops console) ──────────────────────────────────────


class AdminUserDetail(BaseModel):
    """Full per-user detail for the ops console."""

    id: str
    email: str
    system_role: str
    plan: str
    plan_status: str | None
    created_at: datetime
    last_sign_in_at: datetime | None
    is_forbidden: bool
    referral_code: str | None
    referred_by: str | None
    referral_count: int
    daily_limit_override: int | None
    credit_usage_reset_at: datetime | None
    # Credit standing
    daily_limit: int
    used: int
    remaining: int
    bonus_daily_tokens: int
    recent_runs: list[dict]

    @field_serializer("created_at", "credit_usage_reset_at", "last_sign_in_at")
    def _ser_datetime(self, value: datetime | None) -> datetime | None:
        return admin_ops.utc(value)


@router.get("/users/{user_id}", response_model=AdminUserDetail)
async def user_detail(user_id: str, request: Request) -> AdminUserDetail:
    """Full detail for one user: profile, credit standing, recent runs. Admin only."""
    await require_admin_user(request, detail=_ADMIN_REQUIRED_DETAIL)
    provider = get_local_provider()
    user = await provider.get_user(user_id)
    if user is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="User not found")

    balance = await get_balance(user)
    referral_count = await provider.count_users_referred_by(user.referral_code) if user.referral_code else 0
    runs = await admin_ops.recent_runs_for_user(user_id, limit=10)

    await admin_ops.record_audit(
        actor=_actor(request),
        action="view-user-detail",
        target_user_id=user_id,
        payload={"runs_returned": len(runs)},
        request=request,
    )

    return AdminUserDetail(
        id=str(user.id),
        email=user.email,
        system_role=user.system_role,
        plan=user.plan,
        plan_status=user.plan_status,
        created_at=user.created_at,
        last_sign_in_at=user.last_sign_in_at,
        is_forbidden=user.is_forbidden,
        referral_code=user.referral_code,
        referred_by=user.referred_by,
        referral_count=referral_count,
        daily_limit_override=user.daily_limit_override,
        credit_usage_reset_at=user.credit_usage_reset_at,
        daily_limit=balance.daily_limit,
        used=balance.used,
        remaining=balance.remaining,
        bonus_daily_tokens=balance.bonus_daily_tokens,
        recent_runs=runs,
    )


class AdminConversationRow(BaseModel):
    """One thread in a user's conversation list (metadata only, no message content)."""

    thread_id: str
    display_name: str | None
    status: str
    created_at: str
    updated_at: str


class AdminConversationsResponse(BaseModel):
    data: list[AdminConversationRow]


@router.get("/users/{user_id}/conversations", response_model=AdminConversationsResponse)
async def list_user_conversations(
    user_id: str,
    request: Request,
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
) -> AdminConversationsResponse:
    """List one user's threads. Admin only. Logged to the audit trail since
    it is a step toward reading that user's private conversation content.
    """
    await require_admin_user(request, detail=_ADMIN_REQUIRED_DETAIL)
    provider = get_local_provider()
    if await provider.get_user(user_id) is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="User not found")

    sf = get_session_factory()
    if sf is None:
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=_NO_SQL_BACKEND_DETAIL)

    rows = await ThreadMetaRepository(sf).search(user_id=user_id, limit=limit, offset=offset)
    await admin_ops.record_audit(
        actor=_actor(request),
        action="view-conversations",
        target_user_id=user_id,
        payload={"limit": limit, "offset": offset},
        request=request,
    )
    return AdminConversationsResponse(
        data=[
            AdminConversationRow(
                thread_id=r["thread_id"],
                display_name=r.get("display_name"),
                status=r.get("status", "idle"),
                created_at=coerce_iso(r.get("created_at", "")),
                updated_at=coerce_iso(r.get("updated_at", "")),
            )
            for r in rows
        ]
    )


class AdminMessagesResponse(BaseModel):
    data: list[dict]


@router.get("/users/{user_id}/conversations/{thread_id}/messages", response_model=AdminMessagesResponse)
async def get_user_conversation_messages(
    user_id: str,
    thread_id: str,
    request: Request,
    limit: int = Query(50, le=200),
    before_seq: int | None = Query(default=None),
    after_seq: int | None = Query(default=None),
) -> AdminMessagesResponse:
    """Full message content for one of a user's threads. Admin only.

    Verifies ``thread_id`` actually belongs to ``user_id`` before returning
    anything, so an admin cannot read another user's messages by guessing
    thread ids. Every read is written to the audit trail since this is the
    one admin surface that exposes private message text.
    """
    await require_admin_user(request, detail=_ADMIN_REQUIRED_DETAIL)

    sf = get_session_factory()
    if sf is None:
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=_NO_SQL_BACKEND_DETAIL)

    if not await ThreadMetaRepository(sf).check_access(thread_id, user_id, require_existing=True):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Thread not found")

    event_store = DbRunEventStore(sf)
    # Ownership was already verified against the thread above; bypass this
    # store's own per-message owner filter (which otherwise defaults to the
    # *caller's* — i.e. the admin's — contextvar user_id, not the target
    # user's) so every message in the thread is returned regardless of the
    # per-row user_id (None for pre-auth legacy rows).
    messages = await event_store.list_messages(thread_id, limit=limit, before_seq=before_seq, after_seq=after_seq, user_id=None)
    await admin_ops.record_audit(
        actor=_actor(request),
        action="view-conversation-messages",
        target_user_id=user_id,
        payload={"thread_id": thread_id, "limit": limit},
        request=request,
    )
    return AdminMessagesResponse(data=messages)


@router.get("/users/{user_id}/channels", response_model=ChannelConnectionsResponse)
async def list_user_channel_connections(user_id: str, request: Request) -> ChannelConnectionsResponse:
    """List one user's IM channel connections (no raw credentials). Admin only.

    Logged to the audit trail since it reveals which external platform
    accounts (Telegram, Slack, Feishu, ...) are bound to this user.
    """
    await require_admin_user(request, detail=_ADMIN_REQUIRED_DETAIL)
    provider = get_local_provider()
    if await provider.get_user(user_id) is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="User not found")

    config = await _get_channel_connections_config(request)
    if not config.enabled:
        return ChannelConnectionsResponse(connections=[])

    repo = _get_repository(request, config)
    rows = await repo.list_connections(user_id)
    await admin_ops.record_audit(
        actor=_actor(request),
        action="view-channel-connections",
        target_user_id=user_id,
        payload={"count": len(rows)},
        request=request,
    )
    return ChannelConnectionsResponse(connections=[ChannelConnectionResponse(**row) for row in rows])


@router.delete("/users/{user_id}/channels/{connection_id}", status_code=status.HTTP_204_NO_CONTENT)
async def revoke_user_channel_connection(user_id: str, connection_id: str, request: Request) -> Response:
    """Revoke one of a user's IM channel connections. Admin only."""
    await require_admin_user(request, detail=_ADMIN_REQUIRED_DETAIL)
    provider = get_local_provider()
    if await provider.get_user(user_id) is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="User not found")

    config = await _get_channel_connections_config(request)
    if not config.enabled:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Channel connections are disabled")

    repo = _get_repository(request, config)
    disconnected = await repo.disconnect_connection(connection_id=connection_id, owner_user_id=user_id)
    if not disconnected:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Channel connection not found")

    await admin_ops.record_audit(
        actor=_actor(request),
        action="revoke-channel-connection",
        target_user_id=user_id,
        payload={"connection_id": connection_id},
        request=request,
    )
    return Response(status_code=status.HTTP_204_NO_CONTENT)


class MessageResponse(BaseModel):
    message: str


@router.post("/users/{user_id}/conversations/{thread_id}/runs/{run_id}/cancel", response_model=MessageResponse)
async def cancel_user_run(
    user_id: str,
    thread_id: str,
    run_id: str,
    request: Request,
    action: Literal["interrupt", "rollback"] = Query(default="interrupt", description="Cancel action"),
) -> MessageResponse:
    """Cancel another user's run. Admin only.

    Verifies ``thread_id`` belongs to ``user_id`` first, same as
    ``get_user_conversation_messages``, so an admin cannot cancel a run on a
    thread by guessing ids. Logged to the audit trail since this stops a
    user's in-flight work.
    """
    await require_admin_user(request, detail=_ADMIN_REQUIRED_DETAIL)

    sf = get_session_factory()
    if sf is None:
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=_NO_SQL_BACKEND_DETAIL)

    if not await ThreadMetaRepository(sf).check_access(thread_id, user_id, require_existing=True):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Thread not found")

    run_svc = get_run_service(request)
    detail = await run_svc.get(run_id)
    if detail is None or detail.thread_id != thread_id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"Run {run_id} not found")

    cancelled = await run_svc.cancel(run_id, action=action)
    if not cancelled:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=_cancel_conflict_detail(run_id, detail))

    await admin_ops.record_audit(
        actor=_actor(request),
        action="cancel-user-run",
        target_user_id=user_id,
        payload={"thread_id": thread_id, "run_id": run_id, "action": action},
        request=request,
    )
    return MessageResponse(message="Run cancelled")


@router.post("/users/{user_id}/reset-usage", response_model=MessageResponse)
async def reset_usage(user_id: str, request: Request) -> MessageResponse:
    """Reset a user's daily usage to full for today. Admin only."""
    await require_admin_user(request, detail=_ADMIN_REQUIRED_DETAIL)
    provider = get_local_provider()
    user = await provider.get_user(user_id)
    if user is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="User not found")
    user.credit_usage_reset_at = datetime.now(UTC)
    await provider.update_user(user)
    await admin_ops.record_audit(actor=_actor(request), action="reset-usage", target_user_id=user_id, payload={}, request=request)
    return MessageResponse(message="Usage reset")


class GrantRequest(BaseModel):
    daily_bonus_tokens: int = Field(..., gt=0, le=100_000_000)
    days: int = Field(..., gt=0, le=365)


@router.post("/users/{user_id}/grant", response_model=MessageResponse)
async def grant_credits(user_id: str, body: GrantRequest, request: Request) -> MessageResponse:
    """Grant a user a temporary daily token bonus. Admin only."""
    await require_admin_user(request, detail=_ADMIN_REQUIRED_DETAIL)
    provider = get_local_provider()
    if await provider.get_user(user_id) is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="User not found")
    await _add_grant(
        user_id,
        daily_bonus_tokens=body.daily_bonus_tokens,
        days=body.days,
        reason="admin_grant",
        session_factory=None,
        now=datetime.now(UTC),
    )
    await admin_ops.record_audit(
        actor=_actor(request),
        action="grant-credits",
        target_user_id=user_id,
        payload={"daily_bonus_tokens": body.daily_bonus_tokens, "days": body.days},
        request=request,
    )
    return MessageResponse(message="Credits granted")


class SetLimitRequest(BaseModel):
    # null clears the override (back to plan default).
    daily_limit_override: int | None = Field(None, ge=0, le=1_000_000_000)


@router.patch("/users/{user_id}/limit", response_model=MessageResponse)
async def set_limit(user_id: str, body: SetLimitRequest, request: Request) -> MessageResponse:
    """Set or clear a user's custom daily token limit. Admin only."""
    await require_admin_user(request, detail=_ADMIN_REQUIRED_DETAIL)
    provider = get_local_provider()
    user = await provider.get_user(user_id)
    if user is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="User not found")
    user.daily_limit_override = body.daily_limit_override
    await provider.update_user(user)
    await admin_ops.record_audit(
        actor=_actor(request),
        action="set-limit",
        target_user_id=user_id,
        payload={"daily_limit_override": body.daily_limit_override},
        request=request,
    )
    return MessageResponse(message="Limit updated")


@router.post("/reset-all-usage", response_model=MessageResponse)
async def reset_all_usage(request: Request) -> MessageResponse:
    """Reset EVERY user's daily usage to full for today. Admin only."""
    await require_admin_user(request, detail=_ADMIN_REQUIRED_DETAIL)
    provider = get_local_provider()
    count = await provider.reset_all_usage(datetime.now(UTC))
    await admin_ops.record_audit(actor=_actor(request), action="reset-all-usage", target_user_id=None, payload={"users": count}, request=request)
    return MessageResponse(message=f"Usage reset for {count} users")


class ActivityResponse(BaseModel):
    data: list[dict]
    limit: int
    offset: int


@router.get("/activity", response_model=ActivityResponse)
async def activity(request: Request, limit: int = Query(50, ge=1, le=200), offset: int = Query(0, ge=0)) -> ActivityResponse:
    """Recent runs across all users (with owner email + tokens). Admin only."""
    await require_admin_user(request, detail=_ADMIN_REQUIRED_DETAIL)
    rows = await admin_ops.recent_activity(limit=limit, offset=offset)
    await admin_ops.record_audit(
        actor=_actor(request),
        action="view-activity",
        target_user_id=None,
        payload={"limit": limit, "offset": offset, "rows": len(rows)},
        request=request,
    )
    return ActivityResponse(data=rows, limit=limit, offset=offset)


class AuditResponse(BaseModel):
    data: list[dict]
    total: int
    limit: int
    offset: int


@router.get("/audit", response_model=AuditResponse)
async def audit(
    request: Request,
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    target_user_id: str | None = Query(default=None, description="Filter to audit rows recorded against this user"),
) -> AuditResponse:
    """The operator-action audit trail, newest first. Admin only."""
    await require_admin_user(request, detail=_ADMIN_REQUIRED_DETAIL)
    rows, total = await admin_ops.list_audit(limit=limit, offset=offset, target_user_id=target_user_id)
    await admin_ops.record_audit(
        actor=_actor(request),
        action="view-audit",
        target_user_id=target_user_id,
        payload={"limit": limit, "offset": offset, "total": total},
        request=request,
    )
    return AuditResponse(data=rows, total=total, limit=limit, offset=offset)


# ── Self-service credit requests (operator inbox) ────────────────────────


class CreditRequestsResponse(BaseModel):
    data: list[dict]
    total: int
    pending: int


@router.get("/credit-requests", response_model=CreditRequestsResponse)
async def list_credit_requests(
    request: Request,
    status_filter: str = Query("pending", pattern="^(pending|approved|declined|all)$"),
    limit: int = Query(100, ge=1, le=200),
    offset: int = Query(0, ge=0),
) -> CreditRequestsResponse:
    """List self-service credit requests for the operator inbox. Admin only."""
    await require_admin_user(request, detail=_ADMIN_REQUIRED_DETAIL)
    from app.gateway import credit_requests as cr

    rows, total = await cr.list_requests(status=None if status_filter == "all" else status_filter, limit=limit, offset=offset)
    pending = await cr.pending_count()
    await admin_ops.record_audit(
        actor=_actor(request),
        action="view-credit-requests",
        target_user_id=None,
        payload={"status_filter": status_filter, "limit": limit, "offset": offset, "total": total},
        request=request,
    )
    return CreditRequestsResponse(data=rows, total=total, pending=pending)


class ResolveRequestBody(BaseModel):
    approve: bool
    # When approving, the bonus to grant (defaults applied if omitted).
    daily_bonus_tokens: int = Field(250_000, gt=0, le=100_000_000)
    days: int = Field(7, gt=0, le=365)


@router.post("/credit-requests/{request_id}/resolve", response_model=MessageResponse)
async def resolve_credit_request(request_id: str, body: ResolveRequestBody, request: Request) -> MessageResponse:
    """Approve (grant credits) or decline a self-service request. Admin only."""
    await require_admin_user(request, detail=_ADMIN_REQUIRED_DETAIL)
    from app.gateway import credit_requests as cr

    existing = await cr.get_request(request_id)
    if existing is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Request not found")
    if existing["status"] != "pending":
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Request already resolved")

    actor = _actor(request)
    if body.approve:
        await _add_grant(
            existing["user_id"],
            daily_bonus_tokens=body.daily_bonus_tokens,
            days=body.days,
            reason="credit_request_approved",
            session_factory=None,
            now=datetime.now(UTC),
        )
    await cr.resolve_request(request_id, approve=body.approve, resolved_by=actor)
    await admin_ops.record_audit(
        actor=actor,
        action="resolve-credit-request",
        target_user_id=existing["user_id"],
        payload={"approve": body.approve, "daily_bonus_tokens": body.daily_bonus_tokens if body.approve else 0, "days": body.days if body.approve else 0},
        request=request,
    )
    return MessageResponse(message="Request approved" if body.approve else "Request declined")


# ── God-mode session control ────────────────────────────────────────────


class ResetPasswordResponse(BaseModel):
    """Operator-facing payload for the admin password reset.

    The new plaintext is returned **once** in the response. The operator
    must capture it before navigating away; the server keeps only the
    bcrypt hash. The CLI tool (``reset_admin.py``) writes the same value
    to a 0600 file for headless operations; the HTTP path returns it
    inline so the console can display and copy it.
    """

    email: str
    new_password: str


@router.post("/users/{user_id}/reset-password", response_model=ResetPasswordResponse)
async def reset_user_password(user_id: str, request: Request) -> ResetPasswordResponse:
    """Generate a fresh password for a user, sign them out everywhere.

    Every call bumps ``token_version`` (invalidating every existing JWT for
    the user), rotates the bcrypt hash, and marks ``needs_setup=True`` so
    the next login forces the user through the change-password flow.

    The new plaintext is returned **once** in the response body. The bytes
    never touch the DB (only the bcrypt hash) and never touch the audit
    log. The action is `reset-password` on the trail so the operator's
    identity is recorded.
    """
    from app.gateway.auth.repositories.sqlite import SQLiteUserRepository
    from app.gateway.auth.reset_admin import reset_user_password as _reset
    from deerflow.persistence.engine import get_session_factory

    await require_admin_user(request, detail=_ADMIN_REQUIRED_DETAIL)
    provider = get_local_provider()
    user = await provider.get_user(user_id)
    if user is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="User not found")

    sf = get_session_factory()
    if sf is None:
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail="Persistence not available")

    result = await _reset(SQLiteUserRepository(sf), user.email)
    if result is None:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Could not reset password")

    user_email, new_password = result
    await admin_ops.record_audit(
        actor=_actor(request),
        action="reset-password",
        target_user_id=user_id,
        payload={"needs_setup": True},
        request=request,
    )
    return ResetPasswordResponse(email=user_email, new_password=new_password)


@router.post("/users/{user_id}/revoke-sessions", response_model=MessageResponse)
async def revoke_user_sessions(user_id: str, request: Request) -> MessageResponse:
    """Sign a user out everywhere without changing the password.

    Bumps ``token_version`` so every existing JWT for the user is rejected
    on the next request. The user keeps their password and can log in
    again with the next session cookie. Use ``reset-password`` when the
    goal is to lock them out AND rotate the secret.
    """
    await require_admin_user(request, detail=_ADMIN_REQUIRED_DETAIL)
    provider = get_local_provider()
    user = await provider.get_user(user_id)
    if user is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="User not found")
    user.token_version += 1
    await provider.update_user(user)
    await admin_ops.record_audit(
        actor=_actor(request),
        action="revoke-sessions",
        target_user_id=user_id,
        payload={"token_version": user.token_version},
        request=request,
    )
    return MessageResponse(message="All sessions revoked")


@router.post("/users/{user_id}/forbid", response_model=MessageResponse)
async def forbid_user(user_id: str, request: Request) -> MessageResponse:
    """Ban a user: reject login at the middleware without deleting the row.

    The AuthMiddleware checks ``is_forbidden`` before any password / OAuth
    path runs, so the user's next login attempt returns 403. The user
    keeps their data so the operator can audit, then ``unforbid`` to
    restore access. Admins can still reach the user via the ops console
    because admin operations authenticate via the ops service token
    (synthetic admin user), bypassing the session branch.
    """
    await require_admin_user(request, detail=_ADMIN_REQUIRED_DETAIL)
    provider = get_local_provider()
    user = await provider.get_user(user_id)
    if user is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="User not found")
    if user.is_forbidden:
        return MessageResponse(message="User is already forbidden")
    user.is_forbidden = True
    user.token_version += 1  # sign them out everywhere too
    await provider.update_user(user)
    await admin_ops.record_audit(
        actor=_actor(request),
        action="forbid-user",
        target_user_id=user_id,
        payload={"also_revoked_sessions": True},
        request=request,
    )
    return MessageResponse(message="User forbidden")


@router.post("/users/{user_id}/unforbid", response_model=MessageResponse)
async def unforbid_user(user_id: str, request: Request) -> MessageResponse:
    """Reverse :func:`forbid_user`. The user can log in again."""
    await require_admin_user(request, detail=_ADMIN_REQUIRED_DETAIL)
    provider = get_local_provider()
    user = await provider.get_user(user_id)
    if user is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="User not found")
    if not user.is_forbidden:
        return MessageResponse(message="User is not forbidden")
    user.is_forbidden = False
    await provider.update_user(user)
    await admin_ops.record_audit(
        actor=_actor(request),
        action="unforbid-user",
        target_user_id=user_id,
        payload={},
        request=request,
    )
    return MessageResponse(message="User restored")


class UserSessionEvent(BaseModel):
    """One auth event in a user's session history."""

    id: str
    action: str
    actor: str
    payload: dict
    actor_ip: str | None = None
    actor_user_agent: str | None = None
    created_at: datetime

    @field_serializer("created_at")
    def _ser(self, value: datetime) -> datetime:
        return admin_ops.utc(value)


class UserSessionsResponse(BaseModel):
    data: list[UserSessionEvent]


@router.get("/users/{user_id}/sessions", response_model=UserSessionsResponse)
async def user_sessions(
    user_id: str,
    request: Request,
    limit: int = Query(50, ge=1, le=200),
) -> UserSessionsResponse:
    """Auth history for a user from the audit trail.

    Surface a chronological list of (login, logout, change-password,
    update-email, failed-login, reset-password, revoke-sessions) for one
    user, newest first. Built from ``admin_ops.user_auth_history`` which
    joins the audit table on ``target_user_id`` and the actor's email.
    """
    await require_admin_user(request, detail=_ADMIN_REQUIRED_DETAIL)
    provider = get_local_provider()
    if await provider.get_user(user_id) is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="User not found")
    rows = await admin_ops.user_auth_history(user_id=user_id, limit=limit)
    await admin_ops.record_audit(
        actor=_actor(request),
        action="view-sessions",
        target_user_id=user_id,
        payload={"limit": limit, "rows": len(rows)},
        request=request,
    )
    return UserSessionsResponse(data=[UserSessionEvent(**row) for row in rows])


# ── God-mode BYOK (Bring-Your-Own-Key) management ───────────────────────


class ByokAdminResponse(BaseModel):
    """One user's BYOK state, as visible to an admin."""

    enabled: bool
    has_key: bool
    provider: str | None = None
    created_at: datetime | None = None
    updated_at: datetime | None = None

    @field_serializer("created_at", "updated_at")
    def _ser(self, value: datetime | None) -> datetime | None:
        return admin_ops.utc(value)


@router.get("/users/{user_id}/byok", response_model=ByokAdminResponse)
async def user_byok_status(user_id: str, request: Request) -> ByokAdminResponse:
    """Read a user's BYOK state. Admin only.

    The encrypted key is never returned; only the metadata (provider,
    timestamps, "have a key" boolean).
    """
    from app.gateway import byok

    await require_admin_user(request, detail=_ADMIN_REQUIRED_DETAIL)
    provider = get_local_provider()
    if await provider.get_user(user_id) is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="User not found")

    if not byok.byok_enabled():
        return ByokAdminResponse(enabled=False, has_key=False)

    row = await byok.get_key_row(user_id)
    await admin_ops.record_audit(
        actor=_actor(request),
        action="view-user-byok",
        target_user_id=user_id,
        payload={"has_key": row is not None},
        request=request,
    )
    return ByokAdminResponse(
        enabled=True,
        has_key=row is not None,
        provider=row.provider if row is not None else None,
        created_at=row.created_at if row is not None else None,
        updated_at=row.updated_at if row is not None else None,
    )


@router.delete("/users/{user_id}/byok", response_model=MessageResponse)
async def revoke_user_byok(user_id: str, request: Request) -> MessageResponse:
    """Wipe a user's stored BYOK key. Admin only.

    The user can re-upload a new key via the self-service route; until
    they do, requests that need the key will fail. The action is audited
    so the operator's identity is on the trail.
    """
    from app.gateway import byok

    await require_admin_user(request, detail=_ADMIN_REQUIRED_DETAIL)
    provider = get_local_provider()
    if await provider.get_user(user_id) is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="User not found")

    await byok.clear_user_key(user_id)
    await admin_ops.record_audit(
        actor=_actor(request),
        action="revoke-user-byok",
        target_user_id=user_id,
        payload={},
        request=request,
    )
    return MessageResponse(message="BYOK key revoked")
