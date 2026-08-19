"""Credit-request service — the self-service side of the hybrid credit wall.

Users submit a request for more daily tokens; operators approve (which
creates a credit grant) or decline. One active pending request per user
(re-submitting updates the existing pending row).
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime
from uuid import uuid4

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.gateway.admin_ops import utc
from deerflow.persistence.credit_request.model import CreditRequestRow
from deerflow.persistence.engine import commit_with_lock_retry, get_session_factory

logger = logging.getLogger(__name__)


def _row_to_dict(r: CreditRequestRow) -> dict:
    return {
        "id": r.id,
        "user_id": r.user_id,
        "email": r.email,
        "reason": r.reason,
        "requested_tokens": r.requested_tokens,
        "status": r.status,
        "created_at": utc(r.created_at),
        "resolved_at": utc(r.resolved_at),
        "resolved_by": r.resolved_by,
    }


async def submit_request(
    user_id: str,
    email: str,
    *,
    reason: str | None,
    requested_tokens: int | None,
    session_factory: async_sessionmaker[AsyncSession] | None = None,
) -> dict:
    """Create or update the user's pending request. Returns the request."""
    sf = session_factory or get_session_factory()
    if sf is None:
        raise RuntimeError("Persistence not available")
    async with sf() as session:
        stmt = select(CreditRequestRow).where(
            CreditRequestRow.user_id == str(user_id),
            CreditRequestRow.status == "pending",
        )
        existing = (await session.execute(stmt)).scalar_one_or_none()
        if existing is not None:
            existing.reason = reason
            existing.requested_tokens = requested_tokens
            existing.created_at = datetime.now(UTC)
            row = existing
        else:
            row = CreditRequestRow(
                id=uuid4().hex,
                user_id=str(user_id),
                email=email,
                reason=reason,
                requested_tokens=requested_tokens,
                status="pending",
                created_at=datetime.now(UTC),
            )
            session.add(row)
        # commit_with_lock_retry may rollback and retry; re-attach the row
        # after each rollback so the post-commit refresh sees a persistent
        # instance. Same pattern as ThreadMetaRepository.create.
        await commit_with_lock_retry(
            session,
            logger_=logger,
            on_retry=lambda: session.add(row),
        )
        return _row_to_dict(row)


async def get_my_latest(
    user_id: str,
    *,
    session_factory: async_sessionmaker[AsyncSession] | None = None,
) -> dict | None:
    sf = session_factory or get_session_factory()
    if sf is None:
        return None
    async with sf() as session:
        stmt = select(CreditRequestRow).where(CreditRequestRow.user_id == str(user_id)).order_by(CreditRequestRow.created_at.desc()).limit(1)
        row = (await session.execute(stmt)).scalar_one_or_none()
        return _row_to_dict(row) if row is not None else None


async def list_requests(
    *,
    status: str | None = "pending",
    limit: int = 100,
    offset: int = 0,
    session_factory: async_sessionmaker[AsyncSession] | None = None,
) -> tuple[list[dict], int]:
    sf = session_factory or get_session_factory()
    if sf is None:
        return [], 0
    from sqlalchemy import func

    async with sf() as session:
        base = select(CreditRequestRow)
        count_q = select(func.count()).select_from(CreditRequestRow)
        if status:
            base = base.where(CreditRequestRow.status == status)
            count_q = count_q.where(CreditRequestRow.status == status)
        total = int(await session.scalar(count_q) or 0)
        rows = (await session.execute(base.order_by(CreditRequestRow.created_at.desc()).limit(limit).offset(offset))).scalars().all()
        return [_row_to_dict(r) for r in rows], total


async def get_request(
    request_id: str,
    *,
    session_factory: async_sessionmaker[AsyncSession] | None = None,
) -> dict | None:
    sf = session_factory or get_session_factory()
    if sf is None:
        return None
    async with sf() as session:
        row = await session.get(CreditRequestRow, request_id)
        return _row_to_dict(row) if row is not None else None


async def resolve_request(
    request_id: str,
    *,
    approve: bool,
    resolved_by: str,
    session_factory: async_sessionmaker[AsyncSession] | None = None,
) -> dict | None:
    """Mark a request approved/declined. Granting is done by the caller."""
    sf = session_factory or get_session_factory()
    if sf is None:
        return None
    async with sf() as session:
        row = await session.get(CreditRequestRow, request_id)
        if row is None:
            return None
        row.status = "approved" if approve else "declined"
        row.resolved_at = datetime.now(UTC)
        row.resolved_by = resolved_by
        # Re-attach the row after each rollback — see commit_with_lock_retry.
        await commit_with_lock_retry(
            session,
            logger_=logger,
            on_retry=lambda: session.add(row),
        )
        return _row_to_dict(row)


async def pending_count(
    *,
    session_factory: async_sessionmaker[AsyncSession] | None = None,
) -> int:
    sf = session_factory or get_session_factory()
    if sf is None:
        return 0
    from sqlalchemy import func

    async with sf() as session:
        stmt = select(func.count()).select_from(CreditRequestRow).where(CreditRequestRow.status == "pending")
        return int(await session.scalar(stmt) or 0)
