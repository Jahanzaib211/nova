"""Nova Plus billing via Stripe.

Gated on ``STRIPE_SECRET_KEY``: when unset, billing is disabled and the
endpoints return 503. Stripe is imported lazily so the dependency is only
touched when billing is actually configured.

The webhook handler is the source of truth for plan state — checkout success
and subscription lifecycle events drive ``user.plan`` / ``plan_status`` /
``plan_renews_at`` / Stripe id linkage. Everything is written through the
auth provider so the change is visible to the credit system immediately.
"""

from __future__ import annotations

import logging
import os
from datetime import UTC, datetime

logger = logging.getLogger(__name__)

STRIPE_SECRET_KEY_ENV = "STRIPE_SECRET_KEY"
STRIPE_WEBHOOK_SECRET_ENV = "STRIPE_WEBHOOK_SECRET"
STRIPE_PRICE_ID_PLUS_ENV = "STRIPE_PRICE_ID_PLUS"

# Subscription statuses Stripe considers "the customer is paying".
_ACTIVE_STATUSES = {"active", "trialing"}


def billing_enabled() -> bool:
    """True when a Stripe secret key is configured."""
    return bool(os.environ.get(STRIPE_SECRET_KEY_ENV, "").strip())


def _stripe():
    """Return the configured stripe module, or None when disabled."""
    if not billing_enabled():
        return None
    import stripe

    stripe.api_key = os.environ[STRIPE_SECRET_KEY_ENV].strip()
    return stripe


def _plus_price_id() -> str | None:
    return os.environ.get(STRIPE_PRICE_ID_PLUS_ENV, "").strip() or None


async def _ensure_customer(stripe, user, provider) -> str:
    """Return the user's Stripe customer id, creating + persisting it if new."""
    if user.stripe_customer_id:
        return user.stripe_customer_id
    customer = stripe.Customer.create(email=user.email, metadata={"nova_user_id": str(user.id)})
    user.stripe_customer_id = customer["id"]
    await provider.update_user(user)
    return customer["id"]


async def create_checkout_session(user, provider, *, success_url: str, cancel_url: str) -> str:
    """Create a Stripe Checkout Session for Nova Plus; return its URL."""
    stripe = _stripe()
    if stripe is None:
        raise RuntimeError("Billing is not configured")
    price_id = _plus_price_id()
    if not price_id:
        raise RuntimeError("STRIPE_PRICE_ID_PLUS is not configured")

    customer_id = await _ensure_customer(stripe, user, provider)
    session = stripe.checkout.Session.create(
        mode="subscription",
        customer=customer_id,
        line_items=[{"price": price_id, "quantity": 1}],
        success_url=success_url,
        cancel_url=cancel_url,
        metadata={"nova_user_id": str(user.id)},
    )
    return session["url"]


async def create_portal_session(user, provider, *, return_url: str) -> str:
    """Create a Stripe customer-portal session; return its URL."""
    stripe = _stripe()
    if stripe is None:
        raise RuntimeError("Billing is not configured")
    customer_id = await _ensure_customer(stripe, user, provider)
    session = stripe.billing_portal.Session.create(customer=customer_id, return_url=return_url)
    return session["url"]


def verify_webhook(payload: bytes, sig_header: str):
    """Verify a webhook signature and return the parsed event (raises on failure)."""
    stripe = _stripe()
    if stripe is None:
        raise RuntimeError("Billing is not configured")
    secret = os.environ.get(STRIPE_WEBHOOK_SECRET_ENV, "").strip()
    if not secret:
        raise RuntimeError("STRIPE_WEBHOOK_SECRET is not configured")
    return stripe.Webhook.construct_event(payload, sig_header, secret)


def _period_end(subscription: dict) -> datetime | None:
    ts = subscription.get("current_period_end")
    return datetime.fromtimestamp(ts, tz=UTC) if ts else None


async def apply_event(event: dict, provider) -> bool:
    """Apply a Stripe event to the corresponding user's plan. Idempotent.

    Returns True when a user was updated, False when the event was ignored
    (unknown type or no matching user). Kept free of the stripe SDK so it can
    be unit-tested with plain dicts.
    """
    etype = event.get("type", "")
    obj = event.get("data", {}).get("object", {}) or {}

    if etype == "checkout.session.completed":
        customer_id = obj.get("customer")
        user_id = (obj.get("metadata") or {}).get("nova_user_id")
        user = None
        if user_id:
            user = await provider.get_user(user_id)
        if user is None and customer_id:
            user = await provider.get_user_by_stripe_customer_id(customer_id)
        if user is None:
            logger.warning("checkout.session.completed for unknown user (customer=%s)", customer_id)
            return False
        user.stripe_customer_id = customer_id or user.stripe_customer_id
        user.stripe_subscription_id = obj.get("subscription") or user.stripe_subscription_id
        user.plan = "plus"
        user.plan_status = "active"
        await provider.update_user(user)
        return True

    if etype in ("customer.subscription.updated", "customer.subscription.created", "customer.subscription.deleted"):
        customer_id = obj.get("customer")
        user = await provider.get_user_by_stripe_customer_id(customer_id) if customer_id else None
        if user is None:
            logger.warning("%s for unknown customer %s", etype, customer_id)
            return False
        status = obj.get("status", "")
        if etype == "customer.subscription.deleted" or status not in _ACTIVE_STATUSES:
            # Lapsed / cancelled → drop back to the free tier.
            user.plan = "free" if etype == "customer.subscription.deleted" else user.plan
            user.plan_status = "canceled" if etype == "customer.subscription.deleted" else status
            if etype == "customer.subscription.deleted":
                user.stripe_subscription_id = None
                user.plan_renews_at = None
        else:
            user.plan = "plus"
            user.plan_status = status
            user.stripe_subscription_id = obj.get("id") or user.stripe_subscription_id
            user.plan_renews_at = _period_end(obj)
        await provider.update_user(user)
        return True

    return False
