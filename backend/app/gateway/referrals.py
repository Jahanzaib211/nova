"""Referral flywheel — invite codes and double-sided token bonuses.

When a new user signs up through someone's invite code, two time-limited
credit grants are created:

- **Welcome boost** (new user): a front-loaded bonus that, for a free
  account, doubles the daily allowance for the first week — an instant
  "I'm rich" hook.
- **Referrer boost** (inviter): a smaller daily bonus for a month per
  successful invite, so referrers keep inviting to keep their boost alive.

Grants are stored in ``credit_grants`` and summed by the credit system.
"""

from __future__ import annotations

import secrets
from datetime import UTC, datetime, timedelta
from uuid import uuid4

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from deerflow.persistence.credit_grant.model import CreditGrantRow
from deerflow.persistence.engine import get_session_factory

# ── Bonus policy (see plan) ──────────────────────────────────────────────
# Welcome boost doubles a free account's 250k/day to 500k/day for a week.
WELCOME_BONUS_DAILY_TOKENS = 250_000
WELCOME_BONUS_DAYS = 7
# Referrer earns +100k/day for a month per friend who joins.
REFERRER_BONUS_DAILY_TOKENS = 100_000
REFERRER_BONUS_DAYS = 30

REASON_WELCOME = "referral_welcome"
REASON_REFERRER = "referral_referrer"

# Unambiguous alphabet (no 0/O/1/I) for human-shareable codes.
_CODE_ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"
_CODE_LEN = 8


def generate_referral_code() -> str:
    """Generate a random, human-friendly 8-char referral code."""
    return "".join(secrets.choice(_CODE_ALPHABET) for _ in range(_CODE_LEN))


async def ensure_referral_code(user, provider) -> str:
    """Return the user's referral code, generating + persisting one if absent.

    Retries on the (astronomically unlikely) unique-collision so existing
    accounts that predate referral codes get one on first access.
    """
    if user.referral_code:
        return user.referral_code

    for _ in range(5):
        code = generate_referral_code()
        if await provider.get_user_by_referral_code(code) is not None:
            continue
        user.referral_code = code
        await provider.update_user(user)
        return code
    # Fall back to a UUID-derived code; collision here is effectively impossible.
    user.referral_code = uuid4().hex[:_CODE_LEN].upper()
    await provider.update_user(user)
    return user.referral_code


async def _add_grant(
    user_id: str,
    *,
    daily_bonus_tokens: int,
    days: int,
    reason: str,
    session_factory: async_sessionmaker[AsyncSession] | None,
    now: datetime,
) -> None:
    sf = session_factory or get_session_factory()
    async with sf() as session:
        session.add(
            CreditGrantRow(
                id=uuid4().hex,
                user_id=str(user_id),
                daily_bonus_tokens=daily_bonus_tokens,
                expires_at=now + timedelta(days=days),
                reason=reason,
                created_at=now,
            )
        )
        await session.commit()


async def apply_referral(
    *,
    new_user,
    referrer,
    session_factory: async_sessionmaker[AsyncSession] | None = None,
    now: datetime | None = None,
) -> None:
    """Create the welcome + referrer grants for a successful referral."""
    moment = now or datetime.now(UTC)
    await _add_grant(
        str(new_user.id),
        daily_bonus_tokens=WELCOME_BONUS_DAILY_TOKENS,
        days=WELCOME_BONUS_DAYS,
        reason=REASON_WELCOME,
        session_factory=session_factory,
        now=moment,
    )
    await _add_grant(
        str(referrer.id),
        daily_bonus_tokens=REFERRER_BONUS_DAILY_TOKENS,
        days=REFERRER_BONUS_DAYS,
        reason=REASON_REFERRER,
        session_factory=session_factory,
        now=moment,
    )
