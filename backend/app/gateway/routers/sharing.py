"""Public read-only thread sharing.

A user opts a thread in with ``POST /api/threads/{thread_id}/share``. The
gateway stores an unguessable token and serves a sanitized, read-only view
of the conversation under ``GET /api/share/{token}`` with **no
authentication**. POSTing again is idempotent (returns the existing token);
``DELETE /api/threads/{thread_id}/share`` revokes it.

Shared views are intentionally thin: only ``event_type``, ``content``,
``created_at``, ``seq`` and ``run_id`` are exposed per message — the raw
``metadata`` blob (tool payloads, file provenance, per-run internals) is
never passed through, and no user identity from the conversation is
included. The frontend composes the public URL from its own origin
(``/share/{token}``); the gateway only returns the token so the URL is
always correct regardless of the public host behind nginx.
"""

from __future__ import annotations

import logging
import secrets
from datetime import datetime
from typing import Any

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel
from sqlalchemy import delete, select

from app.gateway.authz import require_permission
from app.gateway.deps import get_run_event_store, get_thread_store
from deerflow.persistence.engine import get_session_factory
from deerflow.persistence.shared_thread.model import SharedThreadRow

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api", tags=["sharing"])


class ShareThreadResponse(BaseModel):
    """Result of creating (or fetching) a share link for a thread."""

    token: str
    thread_id: str
    shared: bool


class SharedMessage(BaseModel):
    """One sanitized message in a public thread view."""

    event_type: str
    content: Any = None
    created_at: str | None = None
    seq: int
    run_id: str


class SharedThreadView(BaseModel):
    """Public, read-only view of a shared conversation."""

    token: str
    thread_id: str
    thread_title: str | None = None
    created_at: datetime
    messages: list[SharedMessage]


async def _find_token(token: str) -> SharedThreadRow | None:
    sf = get_session_factory()
    if sf is None:
        raise HTTPException(status_code=503, detail="Persistence not available")
    async with sf() as session:
        row = await session.get(SharedThreadRow, token)
        return row


@router.post("/threads/{thread_id}/share", response_model=ShareThreadResponse)
@require_permission("threads", "write", owner_check=True, require_existing=True)
async def create_share_link(thread_id: str, request: Request) -> ShareThreadResponse:
    """Create (or return the existing) share link for a thread."""
    sf = get_session_factory()
    if sf is None:
        raise HTTPException(status_code=503, detail="Persistence not available")

    async with sf() as session:
        existing = (await session.execute(select(SharedThreadRow).where(SharedThreadRow.thread_id == thread_id).limit(1))).scalar_one_or_none()
        if existing is not None:
            return ShareThreadResponse(token=existing.token, thread_id=thread_id, shared=True)

        token = secrets.token_urlsafe(32)
        session.add(SharedThreadRow(token=token, thread_id=thread_id))
        await session.commit()

    logger.info("Created share link for thread %s", thread_id)
    return ShareThreadResponse(token=token, thread_id=thread_id, shared=True)


@router.delete("/threads/{thread_id}/share", response_model=ShareThreadResponse)
@require_permission("threads", "write", owner_check=True, require_existing=True)
async def revoke_share_link(thread_id: str, request: Request) -> ShareThreadResponse:
    """Revoke the share link for a thread, if one exists."""
    sf = get_session_factory()
    if sf is None:
        raise HTTPException(status_code=503, detail="Persistence not available")

    async with sf() as session:
        rows = (await session.execute(select(SharedThreadRow).where(SharedThreadRow.thread_id == thread_id).limit(1))).scalar_one_or_none()
        token = rows.token if rows else None
        await session.execute(delete(SharedThreadRow).where(SharedThreadRow.thread_id == thread_id))
        await session.commit()

    logger.info("Revoked share link for thread %s", thread_id)
    return ShareThreadResponse(token=token or "", thread_id=thread_id, shared=False)


@router.get("/share/{token}", response_model=SharedThreadView)
async def get_shared_thread(token: str, request: Request) -> SharedThreadView:
    """Public, unauthenticated, read-only view of a shared conversation.

    Only intentionally public fields are returned: message type, content,
    timestamp, sequence and originating run. The message ``metadata`` blob
    and any user identity are deliberately excluded.
    """
    row = await _find_token(token)
    if row is None:
        raise HTTPException(status_code=404, detail="Share link not found or revoked")

    thread_title: str | None = None
    try:
        thread_store = get_thread_store(request)
        meta = await thread_store.get(row.thread_id, user_id=None)
        thread_title = meta.get("display_name") if meta else None
    except Exception:
        logger.debug("Could not resolve title for shared thread %s", row.thread_id)

    event_store = get_run_event_store(request)
    raw_messages = await event_store.list_messages(row.thread_id, limit=200)

    messages = [
        SharedMessage(
            event_type=msg.get("event_type", ""),
            content=msg.get("content"),
            created_at=msg.get("created_at"),
            seq=msg.get("seq", 0),
            run_id=msg.get("run_id", ""),
        )
        for msg in raw_messages
    ]

    return SharedThreadView(
        token=row.token,
        thread_id=row.thread_id,
        thread_title=thread_title,
        created_at=row.created_at,
        messages=messages,
    )
