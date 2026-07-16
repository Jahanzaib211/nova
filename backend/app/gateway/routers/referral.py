"""Referral endpoint — the caller's invite code, stats, and active bonus.

``GET /api/v1/referral`` returns (and lazily creates) the user's invite code
so the account UI can render a shareable link, plus how many people they've
referred and their current bonus token/day.
"""

from __future__ import annotations

from fastapi import APIRouter, Request
from pydantic import BaseModel

from app.gateway.credits import active_bonus_tokens
from app.gateway.deps import get_current_user_from_request, get_local_provider
from app.gateway.referrals import (
    REFERRER_BONUS_DAILY_TOKENS,
    REFERRER_BONUS_DAYS,
    WELCOME_BONUS_DAILY_TOKENS,
    WELCOME_BONUS_DAYS,
    ensure_referral_code,
)

router = APIRouter(prefix="/api/v1/referral", tags=["referral"])


class ReferralResponse(BaseModel):
    """The caller's referral standing."""

    code: str
    referral_count: int
    bonus_daily_tokens: int
    # Static policy, surfaced so the UI can explain the offer without hardcoding.
    welcome_bonus_daily_tokens: int
    welcome_bonus_days: int
    referrer_bonus_daily_tokens: int
    referrer_bonus_days: int


@router.get("", response_model=ReferralResponse)
async def get_referral(request: Request) -> ReferralResponse:
    """Return the caller's invite code (creating one if needed) and stats."""
    user = await get_current_user_from_request(request)
    provider = get_local_provider()

    code = await ensure_referral_code(user, provider)
    count = await provider.count_users_referred_by(code)
    bonus = await active_bonus_tokens(str(user.id))

    return ReferralResponse(
        code=code,
        referral_count=count,
        bonus_daily_tokens=bonus,
        welcome_bonus_daily_tokens=WELCOME_BONUS_DAILY_TOKENS,
        welcome_bonus_days=WELCOME_BONUS_DAYS,
        referrer_bonus_daily_tokens=REFERRER_BONUS_DAILY_TOKENS,
        referrer_bonus_days=REFERRER_BONUS_DAYS,
    )
