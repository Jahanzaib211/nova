"""Contract tests for ``deerflow.jobs.status``.

The job runner's status and event vocabularies are shared with the frontend
through ``contracts/job_status_contract.json``. Both sides load the same file,
so a rename on either side fails a test instead of desynchronising the UI.
"""

from __future__ import annotations

import json
from pathlib import Path

from deerflow.jobs.status import (
    JOB_EVENT_TYPES,
    JOB_STATUSES,
    TERMINAL_JOB_STATUSES,
    JobEventType,
    JobStatus,
    is_terminal,
)

_REPO_ROOT = Path(__file__).resolve().parents[2]
_CONTRACT_PATH = _REPO_ROOT / "contracts" / "job_status_contract.json"


def _load_contract() -> dict:
    return json.loads(_CONTRACT_PATH.read_text(encoding="utf-8"))


def test_contract_file_exists():
    assert _CONTRACT_PATH.is_file(), f"missing shared fixture: {_CONTRACT_PATH}"


def test_statuses_match_contract():
    contract = _load_contract()
    assert list(JOB_STATUSES) == contract["statuses"]
    assert {s.value for s in JobStatus} == set(contract["statuses"])


def test_terminal_statuses_match_contract():
    contract = _load_contract()
    assert set(TERMINAL_JOB_STATUSES) == set(contract["terminal_statuses"])
    assert set(contract["terminal_statuses"]) <= set(contract["statuses"])
    for status in JobStatus:
        assert is_terminal(status) == (status.value in contract["terminal_statuses"])


def test_event_types_match_contract():
    contract = _load_contract()
    assert list(JOB_EVENT_TYPES) == contract["event_types"]
    assert {e.value for e in JobEventType} == set(contract["event_types"])


def test_progress_schema_is_bounded():
    """The UI renders ``pct`` as a bar; both sides rely on the 0..100 bound."""
    schema = _load_contract()["progress_schema"]
    assert schema["properties"]["pct"]["minimum"] == 0
    assert schema["properties"]["pct"]["maximum"] == 100
    assert "pct" in schema["required"]
