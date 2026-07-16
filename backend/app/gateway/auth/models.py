"""User Pydantic models for authentication."""

from datetime import UTC, datetime
from typing import Literal
from uuid import UUID, uuid4

from pydantic import BaseModel, ConfigDict, EmailStr, Field


def _utc_now() -> datetime:
    """Return current UTC time (timezone-aware)."""
    return datetime.now(UTC)


class User(BaseModel):
    """Internal user representation."""

    model_config = ConfigDict(from_attributes=True)

    id: UUID = Field(default_factory=uuid4, description="Primary key")
    email: EmailStr = Field(..., description="Unique email address")
    password_hash: str | None = Field(None, description="bcrypt hash, nullable for OAuth users")
    system_role: Literal["admin", "user"] = Field(default="user")
    created_at: datetime = Field(default_factory=_utc_now)

    # OAuth linkage (optional)
    oauth_provider: str | None = Field(None, description="e.g. 'github', 'google'")
    oauth_id: str | None = Field(None, description="User ID from OAuth provider")

    # Auth lifecycle
    needs_setup: bool = Field(default=False, description="True when a reset account must complete setup")
    token_version: int = Field(default=0, description="Incremented on password change to invalidate old JWTs")

    # Plan / billing
    plan: Literal["free", "plus", "enterprise"] = Field(default="free", description="Entitlement tier")
    plan_status: str | None = Field(None, description="Stripe subscription state: active|past_due|canceled")
    plan_renews_at: datetime | None = Field(None, description="Current billing period end")
    stripe_customer_id: str | None = Field(None, description="Stripe customer id")
    stripe_subscription_id: str | None = Field(None, description="Stripe subscription id")

    # Terms & Conditions consent
    tos_accepted_version: str | None = Field(None, description="TOS version the user accepted")
    tos_accepted_at: datetime | None = Field(None, description="When the user accepted the TOS")

    # Referral flywheel
    referral_code: str | None = Field(None, description="This user's own invite code (unique)")
    referred_by: str | None = Field(None, description="Invite code of the referrer, if any")

    # Operator controls (ops console)
    daily_limit_override: int | None = Field(None, description="Custom daily token limit; overrides the plan allowance when set")
    credit_usage_reset_at: datetime | None = Field(None, description="Operator usage-reset marker; usage counts only after this moment today")


class UserResponse(BaseModel):
    """Response model for user info endpoint."""

    id: str
    email: str
    system_role: Literal["admin", "user"]
    needs_setup: bool = False
    plan: Literal["free", "plus", "enterprise"] = "free"
    plan_status: str | None = None
    tos_accepted_version: str | None = None
    referral_code: str | None = None
