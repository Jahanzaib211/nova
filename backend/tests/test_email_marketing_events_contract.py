"""Contract tests for ``deerflow.email_marketing.events``.

Event, campaign-status, contact-status and suppression vocabularies are shared
with the frontend through ``contracts/email_marketing_events_contract.json``.
"""

from __future__ import annotations

import json
from pathlib import Path

from deerflow.email_marketing.events import (
    CAMPAIGN_STATUSES,
    CONTACT_STATUSES,
    EMAIL_EVENT_TYPES,
    SUPPRESSION_REASONS,
    TERMINAL_CAMPAIGN_STATUSES,
    CampaignStatus,
    ContactStatus,
    EmailEventType,
    SuppressionReason,
)

_REPO_ROOT = Path(__file__).resolve().parents[2]
_CONTRACT_PATH = _REPO_ROOT / "contracts" / "email_marketing_events_contract.json"


def _load_contract() -> dict:
    return json.loads(_CONTRACT_PATH.read_text(encoding="utf-8"))


def test_contract_file_exists():
    assert _CONTRACT_PATH.is_file(), f"missing shared fixture: {_CONTRACT_PATH}"


def test_event_types_match_contract():
    contract = _load_contract()
    assert list(EMAIL_EVENT_TYPES) == contract["event_types"]
    assert {e.value for e in EmailEventType} == set(contract["event_types"])


def test_campaign_statuses_match_contract():
    contract = _load_contract()
    assert list(CAMPAIGN_STATUSES) == contract["campaign_statuses"]
    assert {s.value for s in CampaignStatus} == set(contract["campaign_statuses"])
    assert set(TERMINAL_CAMPAIGN_STATUSES) == set(contract["terminal_campaign_statuses"])
    assert set(TERMINAL_CAMPAIGN_STATUSES) <= set(CAMPAIGN_STATUSES)


def test_contact_statuses_match_contract():
    contract = _load_contract()
    assert list(CONTACT_STATUSES) == contract["contact_statuses"]
    assert {s.value for s in ContactStatus} == set(contract["contact_statuses"])


def test_suppression_reasons_match_contract():
    contract = _load_contract()
    assert list(SUPPRESSION_REASONS) == contract["suppression_reasons"]
    assert {r.value for r in SuppressionReason} == set(contract["suppression_reasons"])
