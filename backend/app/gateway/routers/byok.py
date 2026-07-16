"""BYOK endpoints — manage the caller's own LLM provider API key.

The key is write-only from the client's perspective: it can be set or
cleared, and status reveals only whether one exists (never the value).
"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request, status
from pydantic import BaseModel, Field

from app.gateway import byok
from app.gateway.deps import get_current_user_from_request

router = APIRouter(prefix="/api/v1/byok", tags=["byok"])


class ByokStatusResponse(BaseModel):
    enabled: bool
    has_key: bool
    provider: str | None = None


class SetByokRequest(BaseModel):
    provider: str = Field(default="", max_length=32)
    api_key: str = Field(..., min_length=8, max_length=512)


class MessageResponse(BaseModel):
    message: str


@router.get("", response_model=ByokStatusResponse)
async def byok_status(request: Request) -> ByokStatusResponse:
    """Whether BYOK is enabled and whether the caller has a key set."""
    user = await get_current_user_from_request(request)
    if not byok.byok_enabled():
        return ByokStatusResponse(enabled=False, has_key=False)
    row = await byok.get_key_row(str(user.id))
    return ByokStatusResponse(
        enabled=True,
        has_key=row is not None,
        provider=row.provider if row is not None else None,
    )


@router.post("", response_model=MessageResponse)
async def set_byok(request: Request, body: SetByokRequest) -> MessageResponse:
    """Store (or replace) the caller's encrypted API key."""
    user = await get_current_user_from_request(request)
    if not byok.byok_enabled():
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Bring-your-own-key is not enabled on this instance.")
    await byok.set_user_key(str(user.id), body.provider, body.api_key)
    return MessageResponse(message="API key saved")


@router.delete("", response_model=MessageResponse)
async def clear_byok(request: Request) -> MessageResponse:
    """Remove the caller's stored API key."""
    user = await get_current_user_from_request(request)
    await byok.clear_user_key(str(user.id))
    return MessageResponse(message="API key removed")
