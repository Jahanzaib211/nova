"""Nova credit system — daily token allowance and the usage wall.

Credits are denominated in LLM tokens. Rather than maintaining a parallel
counter, "tokens used today" is computed directly from the ``runs`` table
(each run already persists ``user_id``, ``total_tokens``, and ``created_at``),
making the finalized run totals the single source of truth.

Free accounts get 250k tokens/day; the allowance resets at UTC midnight and
does not roll over. Paid tiers get larger daily ceilings. Referral bonuses
(added later) and bring-your-own-key bypass extend this via
``bonus_daily_tokens`` and the enforcement skip in ``start_run``.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from deerflow.persistence.credit_grant.model import CreditGrantRow
from deerflow.persistence.engine import get_session_factory
from deerflow.persistence.run.model import RunRow

# Base daily token allowance per plan. "enterprise" is effectively uncapped
# for practical single-tenant usage but stays finite to keep arithmetic sane.
DAILY_TOKEN_LIMITS: dict[str, int] = {
    "free": 250_000,
    "plus": 5_000_000,
    "enterprise": 50_000_000,
}
DEFAULT_DAILY_LIMIT = DAILY_TOKEN_LIMITS["free"]


def daily_limit_for_plan(plan: str | None) -> int:
    """Base daily token allowance for a plan (unknown/None → free)."""
    return DAILY_TOKEN_LIMITS.get(plan or "free", DEFAULT_DAILY_LIMIT)


def _start_of_utc_day(now: datetime) -> datetime:
    """Midnight UTC for the day containing ``now`` (timezone-aware)."""
    return now.astimezone(UTC).replace(hour=0, minute=0, second=0, microsecond=0)


async def tokens_used_today(
    user_id: str,
    *,
    session_factory: async_sessionmaker[AsyncSession] | None = None,
    now: datetime | None = None,
    reset_at: datetime | None = None,
) -> int:
    """Sum ``total_tokens`` across the user's runs created since UTC midnight.

    ``reset_at`` is the operator usage-reset marker: when it falls inside the
    current UTC day, counting starts there instead — an ops-console "reset
    usage" instantly restores the full allowance without touching run history.
    """
    sf = session_factory or get_session_factory()
    if sf is None:
        # Persistence not initialised — fail open (report no usage) rather than
        # crash a run. Normal deployments always have an engine.
        return 0
    start = _start_of_utc_day(now or datetime.now(UTC))
    if reset_at is not None:
        marker = reset_at if reset_at.tzinfo else reset_at.replace(tzinfo=UTC)
        start = max(start, marker)
    stmt = select(func.coalesce(func.sum(RunRow.total_tokens), 0)).where(RunRow.user_id == user_id).where(RunRow.created_at >= start)
    async with sf() as session:
        return int(await session.scalar(stmt) or 0)


async def active_bonus_tokens(
    user_id: str,
    *,
    session_factory: async_sessionmaker[AsyncSession] | None = None,
    now: datetime | None = None,
) -> int:
    """Sum daily bonus tokens from the user's currently-active credit grants."""
    sf = session_factory or get_session_factory()
    if sf is None:
        return 0
    moment = now or datetime.now(UTC)
    stmt = select(func.coalesce(func.sum(CreditGrantRow.daily_bonus_tokens), 0)).where(CreditGrantRow.user_id == user_id).where(CreditGrantRow.expires_at > moment)
    async with sf() as session:
        return int(await session.scalar(stmt) or 0)


@dataclass(frozen=True)
class CreditBalance:
    """A user's credit standing for the current UTC day."""

    plan: str
    daily_limit: int
    used: int
    remaining: int
    bonus_daily_tokens: int = 0

    @property
    def exhausted(self) -> bool:
        return self.remaining <= 0


async def get_balance(
    user,
    *,
    session_factory: async_sessionmaker[AsyncSession] | None = None,
    now: datetime | None = None,
    bonus_daily_tokens: int | None = None,
) -> CreditBalance:
    """Compute the user's credit balance for today.

    ``bonus_daily_tokens`` (referral boosts, etc.) is added on top of the
    plan's base allowance. When ``None`` (the default), it is computed from
    the user's active credit grants; pass an explicit int to override.

    An operator-set ``daily_limit_override`` on the user replaces the plan's
    base allowance; ``credit_usage_reset_at`` restarts today's usage count.
    """
    plan = getattr(user, "plan", None) or "free"
    override = getattr(user, "daily_limit_override", None)
    base = override if override is not None and override >= 0 else daily_limit_for_plan(plan)
    if bonus_daily_tokens is None:
        bonus_daily_tokens = await active_bonus_tokens(str(user.id), session_factory=session_factory, now=now)
    bonus = max(0, bonus_daily_tokens)
    limit = base + bonus
    used = await tokens_used_today(
        str(user.id),
        session_factory=session_factory,
        now=now,
        reset_at=getattr(user, "credit_usage_reset_at", None),
    )
    remaining = max(0, limit - used)
    return CreditBalance(
        plan=plan,
        daily_limit=limit,
        used=used,
        remaining=remaining,
        bonus_daily_tokens=bonus,
    )
