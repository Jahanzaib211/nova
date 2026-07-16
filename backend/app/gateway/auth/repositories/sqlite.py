"""SQLAlchemy-backed UserRepository implementation.

Uses the shared async session factory from
``deerflow.persistence.engine`` — the ``users`` table lives in the
same database as ``threads_meta``, ``runs``, ``run_events``, and
``feedback``.

Constructor takes the session factory directly (same pattern as the
other four repositories in ``deerflow.persistence.*``). Callers
construct this after ``init_engine_from_config()`` has run.
"""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy import func, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.gateway.auth.models import User
from app.gateway.auth.repositories.base import UserNotFoundError, UserRepository
from deerflow.persistence.user.model import UserRow


class SQLiteUserRepository(UserRepository):
    """Async user repository backed by the shared SQLAlchemy engine."""

    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        self._sf = session_factory

    # ── Converters ────────────────────────────────────────────────────

    @staticmethod
    def _row_to_user(row: UserRow) -> User:
        return User(
            id=UUID(row.id),
            email=row.email,
            password_hash=row.password_hash,
            system_role=row.system_role,  # type: ignore[arg-type]
            # SQLite loses tzinfo on read; reattach UTC so downstream
            # code can compare timestamps reliably.
            created_at=row.created_at if row.created_at.tzinfo else row.created_at.replace(tzinfo=UTC),
            oauth_provider=row.oauth_provider,
            oauth_id=row.oauth_id,
            needs_setup=row.needs_setup,
            token_version=row.token_version,
            plan=row.plan,  # type: ignore[arg-type]
            plan_status=row.plan_status,
            plan_renews_at=(row.plan_renews_at.replace(tzinfo=UTC) if row.plan_renews_at and not row.plan_renews_at.tzinfo else row.plan_renews_at),
            stripe_customer_id=row.stripe_customer_id,
            stripe_subscription_id=row.stripe_subscription_id,
            tos_accepted_version=row.tos_accepted_version,
            tos_accepted_at=(row.tos_accepted_at.replace(tzinfo=UTC) if row.tos_accepted_at and not row.tos_accepted_at.tzinfo else row.tos_accepted_at),
            referral_code=row.referral_code,
            referred_by=row.referred_by,
            daily_limit_override=row.daily_limit_override,
            credit_usage_reset_at=(row.credit_usage_reset_at.replace(tzinfo=UTC) if row.credit_usage_reset_at and not row.credit_usage_reset_at.tzinfo else row.credit_usage_reset_at),
        )

    @staticmethod
    def _user_to_row(user: User) -> UserRow:
        return UserRow(
            id=str(user.id),
            email=user.email,
            password_hash=user.password_hash,
            system_role=user.system_role,
            created_at=user.created_at,
            oauth_provider=user.oauth_provider,
            oauth_id=user.oauth_id,
            needs_setup=user.needs_setup,
            token_version=user.token_version,
            plan=user.plan,
            plan_status=user.plan_status,
            plan_renews_at=user.plan_renews_at,
            stripe_customer_id=user.stripe_customer_id,
            stripe_subscription_id=user.stripe_subscription_id,
            tos_accepted_version=user.tos_accepted_version,
            tos_accepted_at=user.tos_accepted_at,
            referral_code=user.referral_code,
            referred_by=user.referred_by,
            daily_limit_override=user.daily_limit_override,
            credit_usage_reset_at=user.credit_usage_reset_at,
        )

    # ── CRUD ──────────────────────────────────────────────────────────

    async def create_user(self, user: User) -> User:
        """Insert a new user. Raises ``ValueError`` on duplicate email."""
        row = self._user_to_row(user)
        async with self._sf() as session:
            session.add(row)
            try:
                await session.commit()
            except IntegrityError as exc:
                await session.rollback()
                raise ValueError(f"Email already registered: {user.email}") from exc
        return user

    async def get_user_by_id(self, user_id: str) -> User | None:
        async with self._sf() as session:
            row = await session.get(UserRow, user_id)
            return self._row_to_user(row) if row is not None else None

    async def get_user_by_email(self, email: str) -> User | None:
        stmt = select(UserRow).where(UserRow.email == email)
        async with self._sf() as session:
            result = await session.execute(stmt)
            row = result.scalar_one_or_none()
            return self._row_to_user(row) if row is not None else None

    async def update_user(self, user: User) -> User:
        async with self._sf() as session:
            row = await session.get(UserRow, str(user.id))
            if row is None:
                # Hard fail on concurrent delete: callers (reset_admin,
                # password change handlers, _ensure_admin_user) all
                # fetched the user just before this call, so a missing
                # row here means the row vanished underneath us. Silent
                # success would let the caller log "password reset" for
                # a row that no longer exists.
                raise UserNotFoundError(f"User {user.id} no longer exists")
            row.email = user.email
            row.password_hash = user.password_hash
            row.system_role = user.system_role
            row.oauth_provider = user.oauth_provider
            row.oauth_id = user.oauth_id
            row.needs_setup = user.needs_setup
            row.token_version = user.token_version
            row.plan = user.plan
            row.plan_status = user.plan_status
            row.plan_renews_at = user.plan_renews_at
            row.stripe_customer_id = user.stripe_customer_id
            row.stripe_subscription_id = user.stripe_subscription_id
            row.tos_accepted_version = user.tos_accepted_version
            row.tos_accepted_at = user.tos_accepted_at
            row.referral_code = user.referral_code
            row.referred_by = user.referred_by
            row.daily_limit_override = user.daily_limit_override
            row.credit_usage_reset_at = user.credit_usage_reset_at
            await session.commit()
        return user

    async def count_users(self) -> int:
        stmt = select(func.count()).select_from(UserRow)
        async with self._sf() as session:
            return await session.scalar(stmt) or 0

    async def count_admin_users(self) -> int:
        stmt = select(func.count()).select_from(UserRow).where(UserRow.system_role == "admin")
        async with self._sf() as session:
            return await session.scalar(stmt) or 0

    async def list_users(self, *, limit: int, offset: int) -> list[User]:
        stmt = select(UserRow).order_by(UserRow.created_at.desc()).limit(limit).offset(offset)
        async with self._sf() as session:
            result = await session.execute(stmt)
            return [self._row_to_user(row) for row in result.scalars().all()]

    async def count_users_since(self, since: datetime) -> int:
        stmt = select(func.count()).select_from(UserRow).where(UserRow.created_at >= since)
        async with self._sf() as session:
            return await session.scalar(stmt) or 0

    async def count_users_by_plan(self) -> dict[str, int]:
        stmt = select(UserRow.plan, func.count()).group_by(UserRow.plan)
        async with self._sf() as session:
            result = await session.execute(stmt)
            return {plan: count for plan, count in result.all()}

    async def get_user_by_referral_code(self, code: str) -> User | None:
        stmt = select(UserRow).where(UserRow.referral_code == code)
        async with self._sf() as session:
            result = await session.execute(stmt)
            row = result.scalar_one_or_none()
            return self._row_to_user(row) if row is not None else None

    async def count_users_referred_by(self, code: str) -> int:
        stmt = select(func.count()).select_from(UserRow).where(UserRow.referred_by == code)
        async with self._sf() as session:
            return await session.scalar(stmt) or 0

    async def get_user_by_stripe_customer_id(self, customer_id: str) -> User | None:
        stmt = select(UserRow).where(UserRow.stripe_customer_id == customer_id)
        async with self._sf() as session:
            result = await session.execute(stmt)
            row = result.scalar_one_or_none()
            return self._row_to_user(row) if row is not None else None

    async def reset_all_usage(self, reset_at: datetime) -> int:
        async with self._sf() as session:
            result = await session.execute(update(UserRow).values(credit_usage_reset_at=reset_at))
            await session.commit()
            return result.rowcount or 0

    async def get_user_by_oauth(self, provider: str, oauth_id: str) -> User | None:
        stmt = select(UserRow).where(UserRow.oauth_provider == provider, UserRow.oauth_id == oauth_id)
        async with self._sf() as session:
            result = await session.execute(stmt)
            row = result.scalar_one_or_none()
            return self._row_to_user(row) if row is not None else None
