"""Legal / Terms-of-Service endpoints: current version + consent recording.

- ``GET /api/v1/legal/terms`` (public) exposes the current TOS/Privacy
  versions so the frontend re-acceptance gate can compare them against the
  authenticated user's stored ``tos_accepted_version``.
- ``POST /api/v1/legal/accept`` (auth required) stamps the current TOS
  version + timestamp onto the caller's account — the enterprise consent
  trail. Callable both at first login (post-signup) and after a version bump.
"""

from __future__ import annotations

from datetime import UTC, datetime

from fastapi import APIRouter, Request
from pydantic import BaseModel

from app.gateway.deps import get_current_user_from_request, get_local_provider
from app.gateway.legal import PRIVACY_VERSION, TOS_VERSION

router = APIRouter(prefix="/api/v1/legal", tags=["legal"])


class TermsVersionResponse(BaseModel):
    """Current legal document versions."""

    tos_version: str
    privacy_version: str


class ConsentStatusResponse(BaseModel):
    """The caller's consent state relative to the current TOS version."""

    tos_version: str
    accepted_version: str | None
    accepted: bool


@router.get("/terms", response_model=TermsVersionResponse)
async def terms_version() -> TermsVersionResponse:
    """Return the current TOS / Privacy versions. Public."""
    return TermsVersionResponse(tos_version=TOS_VERSION, privacy_version=PRIVACY_VERSION)


@router.post("/accept", response_model=ConsentStatusResponse)
async def accept_terms(request: Request) -> ConsentStatusResponse:
    """Record the current user's acceptance of the current TOS version."""
    user = await get_current_user_from_request(request)

    # Idempotent: re-accepting the same version just refreshes the timestamp.
    user.tos_accepted_version = TOS_VERSION
    user.tos_accepted_at = datetime.now(UTC)
    await get_local_provider().update_user(user)

    return ConsentStatusResponse(
        tos_version=TOS_VERSION,
        accepted_version=user.tos_accepted_version,
        accepted=True,
    )
