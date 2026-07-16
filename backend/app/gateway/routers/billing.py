"""Nova Plus billing endpoints (Stripe).

- ``GET  /api/v1/billing``          → whether billing is configured + current plan
- ``POST /api/v1/billing/checkout`` → Stripe Checkout Session URL (auth)
- ``POST /api/v1/billing/portal``   → Stripe customer-portal URL (auth)
- ``POST /api/v1/billing/webhook``  → Stripe event sink (public, signature-verified)
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, HTTPException, Request, status
from pydantic import BaseModel

from app.gateway import billing
from app.gateway.deps import get_current_user_from_request, get_local_provider

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1/billing", tags=["billing"])


class BillingStatusResponse(BaseModel):
    enabled: bool
    plan: str
    plan_status: str | None = None


class CheckoutRequest(BaseModel):
    success_url: str | None = None
    cancel_url: str | None = None


class UrlResponse(BaseModel):
    url: str


def _origin(request: Request) -> str:
    return str(request.base_url).rstrip("/")


def _safe_redirect(candidate: str | None, origin: str, default: str) -> str:
    """Return ``candidate`` only when it is an absolute same-origin URL.

    Stripe echoes ``success_url``/``cancel_url`` back as a post-payment browser
    redirect, so a caller-supplied off-origin URL would be an open redirect.
    Anything that is not exactly our origin (or a path beneath it) — including
    off-origin, protocol-relative, or relative values — falls back to the
    trusted default. Stripe requires absolute URLs, so relative inputs are
    intentionally rejected rather than passed through.
    """
    if candidate and (candidate == origin or candidate.startswith(origin + "/")):
        return candidate
    return default


@router.get("", response_model=BillingStatusResponse)
async def billing_status(request: Request) -> BillingStatusResponse:
    """Report whether billing is configured and the caller's current plan."""
    user = await get_current_user_from_request(request)
    return BillingStatusResponse(enabled=billing.billing_enabled(), plan=user.plan, plan_status=user.plan_status)


@router.post("/checkout", response_model=UrlResponse)
async def checkout(request: Request, body: CheckoutRequest) -> UrlResponse:
    """Create a Stripe Checkout Session for Nova Plus."""
    if not billing.billing_enabled():
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail="Billing is not configured on this instance.")
    user = await get_current_user_from_request(request)
    origin = _origin(request)
    try:
        url = await billing.create_checkout_session(
            user,
            get_local_provider(),
            success_url=_safe_redirect(body.success_url, origin, f"{origin}/workspace?upgraded=1"),
            cancel_url=_safe_redirect(body.cancel_url, origin, f"{origin}/saas"),
        )
    except RuntimeError as exc:
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=str(exc)) from exc
    except Exception as exc:  # Stripe/network errors
        logger.exception("Stripe checkout failed")
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail="Could not start checkout.") from exc
    return UrlResponse(url=url)


@router.post("/portal", response_model=UrlResponse)
async def portal(request: Request) -> UrlResponse:
    """Create a Stripe customer-portal session to manage/cancel the subscription."""
    if not billing.billing_enabled():
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail="Billing is not configured on this instance.")
    user = await get_current_user_from_request(request)
    try:
        url = await billing.create_portal_session(user, get_local_provider(), return_url=f"{_origin(request)}/workspace")
    except RuntimeError as exc:
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=str(exc)) from exc
    except Exception as exc:
        logger.exception("Stripe portal failed")
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail="Could not open the billing portal.") from exc
    return UrlResponse(url=url)


@router.post("/webhook")
async def webhook(request: Request) -> dict:
    """Receive and apply Stripe subscription lifecycle events."""
    if not billing.billing_enabled():
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail="Billing is not configured.")
    payload = await request.body()
    sig = request.headers.get("stripe-signature", "")
    try:
        event = billing.verify_webhook(payload, sig)
    except RuntimeError as exc:
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=str(exc)) from exc
    except Exception as exc:  # invalid signature / malformed payload
        logger.warning("Rejected Stripe webhook: %s", exc)
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid webhook signature") from exc

    updated = await billing.apply_event(event, get_local_provider())
    return {"received": True, "updated": updated}
