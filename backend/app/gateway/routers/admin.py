"""Admin-only endpoints: registration visibility, user administration, and
the ops-console god-mode controls (usage reset, credit grants, custom
limits, plan overrides) — every mutation written to the audit trail.

Gated by :func:`require_admin_user`. Reachable by a real admin session or by
the ops console's service token (see ``ops_auth.py``).
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from fastapi import APIRouter, HTTPException, Query, Request, status
from pydantic import BaseModel, Field

from app.gateway import admin_ops
from app.gateway.credits import get_balance
from app.gateway.deps import get_local_provider, require_admin_user
from app.gateway.referrals import _add_grant

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
    await admin_ops.record_audit(actor=_actor(request), action="set-plan", target_user_id=user_id, payload={"plan": body.plan})

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

    return AdminUserDetail(
        id=str(user.id),
        email=user.email,
        system_role=user.system_role,
        plan=user.plan,
        plan_status=user.plan_status,
        created_at=user.created_at,
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


class MessageResponse(BaseModel):
    message: str


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
    await admin_ops.record_audit(actor=_actor(request), action="reset-usage", target_user_id=user_id, payload={})
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
    )
    return MessageResponse(message="Limit updated")


@router.post("/reset-all-usage", response_model=MessageResponse)
async def reset_all_usage(request: Request) -> MessageResponse:
    """Reset EVERY user's daily usage to full for today. Admin only."""
    await require_admin_user(request, detail=_ADMIN_REQUIRED_DETAIL)
    provider = get_local_provider()
    count = await provider.reset_all_usage(datetime.now(UTC))
    await admin_ops.record_audit(actor=_actor(request), action="reset-all-usage", target_user_id=None, payload={"users": count})
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
    return ActivityResponse(data=rows, limit=limit, offset=offset)


class AuditResponse(BaseModel):
    data: list[dict]
    total: int
    limit: int
    offset: int


@router.get("/audit", response_model=AuditResponse)
async def audit(request: Request, limit: int = Query(50, ge=1, le=200), offset: int = Query(0, ge=0)) -> AuditResponse:
    """The operator-action audit trail, newest first. Admin only."""
    await require_admin_user(request, detail=_ADMIN_REQUIRED_DETAIL)
    rows, total = await admin_ops.list_audit(limit=limit, offset=offset)
    return AuditResponse(data=rows, total=total, limit=limit, offset=offset)
