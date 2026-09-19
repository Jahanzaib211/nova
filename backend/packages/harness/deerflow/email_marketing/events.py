"""Email-marketing event, status and suppression vocabularies.

Pinned to ``contracts/email_marketing_events_contract.json`` by
``tests/test_email_marketing_events_contract.py``; the frontend pins the
same file.
"""

from __future__ import annotations

from enum import Enum


class EmailEventType(str, Enum):
    QUEUED = "queued"
    SENT = "sent"
    DEFERRED = "deferred"
    BOUNCED_HARD = "bounced_hard"
    BOUNCED_SOFT = "bounced_soft"
    COMPLAINED = "complained"
    OPENED = "opened"
    CLICKED = "clicked"
    UNSUBSCRIBED = "unsubscribed"
    SUPPRESSED = "suppressed"
    FAILED = "failed"
    # A human answered a campaign mail (reply+<send>@); routed to Chatwoot.
    REPLIED = "replied"


class CampaignStatus(str, Enum):
    DRAFT = "draft"
    SCHEDULED = "scheduled"
    SENDING = "sending"
    PAUSED = "paused"
    COMPLETED = "completed"
    CANCELLED = "cancelled"
    FAILED = "failed"


class ContactStatus(str, Enum):
    PENDING = "pending"
    SUBSCRIBED = "subscribed"
    UNSUBSCRIBED = "unsubscribed"
    BOUNCED = "bounced"
    COMPLAINED = "complained"


class SuppressionReason(str, Enum):
    HARD_BOUNCE = "hard_bounce"
    COMPLAINT = "complaint"
    UNSUBSCRIBE = "unsubscribe"
    MANUAL = "manual"
    INVALID = "invalid"


EMAIL_EVENT_TYPES: tuple[str, ...] = tuple(e.value for e in EmailEventType)
CAMPAIGN_STATUSES: tuple[str, ...] = tuple(s.value for s in CampaignStatus)
CONTACT_STATUSES: tuple[str, ...] = tuple(s.value for s in ContactStatus)
SUPPRESSION_REASONS: tuple[str, ...] = tuple(r.value for r in SuppressionReason)

TERMINAL_CAMPAIGN_STATUSES: frozenset[str] = frozenset(
    {
        CampaignStatus.COMPLETED.value,
        CampaignStatus.CANCELLED.value,
        CampaignStatus.FAILED.value,
    }
)
