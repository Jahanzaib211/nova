"""Credits endpoint — exposes the caller's daily token balance.

Read-only. Powers the credit indicator in the UI so users can see how much
of today's allowance remains before they hit the wall enforced in
``start_run``.
"""

from __future__ import annotations

from fastapi import APIRouter, Request
from pydantic import BaseModel, Field

from app.gateway.credits import get_balance
from app.gateway.deps import get_current_user_from_request

router = APIRouter(prefix="/api/v1/credits", tags=["credits"])


class CreditsResponse(BaseModel):
    """The caller's credit standing for the current UTC day."""

    plan: str
    daily_limit: int
    used: int
    remaining: int
    bonus_daily_tokens: int
    exhausted: bool
    # Admins/operators are not subject to the wall; the UI can hide the meter.
    unlimited: bool
    # The caller's latest self-service request status (pending/approved/declined).
    request_status: str | None = None


@router.get("", response_model=CreditsResponse)
async def get_credits(request: Request) -> CreditsResponse:
    """Return today's token balance for the authenticated user."""
    from app.gateway.byok import has_active_key
    from app.gateway.credit_requests import get_my_latest

    user = await get_current_user_from_request(request)
    balance = await get_balance(user)
    # Not subject to the wall: admins/operators, or users on their own key.
    unlimited = getattr(user, "system_role", None) != "user" or await has_active_key(str(user.id))
    latest = await get_my_latest(str(user.id))
    return CreditsResponse(
        plan=balance.plan,
        daily_limit=balance.daily_limit,
        used=balance.used,
        remaining=balance.remaining,
        bonus_daily_tokens=balance.bonus_daily_tokens,
        exhausted=balance.exhausted and not unlimited,
        unlimited=unlimited,
        request_status=latest["status"] if latest else None,
    )


class CreditRequestBody(BaseModel):
    reason: str | None = Field(None, max_length=1000)
    requested_tokens: int | None = Field(None, ge=0, le=1_000_000_000)


class CreditRequestResponse(BaseModel):
    status: str
    created_at: str | None = None


@router.post("/request", response_model=CreditRequestResponse)
async def request_more_credits(request: Request, body: CreditRequestBody) -> CreditRequestResponse:
    """Submit (or update) a self-service request for more daily credits.

    Lands in the ops console for an operator to approve or decline. This is
    the user-facing half of the hybrid wall.
    """
    from app.gateway.credit_requests import submit_request

    user = await get_current_user_from_request(request)
    result = await submit_request(
        str(user.id),
        user.email,
        reason=body.reason,
        requested_tokens=body.requested_tokens,
    )
    created = result.get("created_at")
    return CreditRequestResponse(status=result["status"], created_at=created.isoformat() if created else None)

