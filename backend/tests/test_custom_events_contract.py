"""Wire-contract test for the four SSE ``custom`` event payloads.

Pinned by the 2026-08-14 audit: the subagent_status family already has its own
contract fixture; the other three custom events did not. This test loads
``contracts/custom_events_contract.json`` and asserts the canonical
sample payloads (the ones each producer in the codebase is expected to emit)
conform. If a future refactor drops a field or changes a type, this test
fails before the frontend's matching parser does.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

_HERE = Path(__file__).resolve().parent
_BACKEND = _HERE.parent
if str(_BACKEND) not in sys.path:
    sys.path.insert(0, str(_BACKEND))


@pytest.fixture(scope="module")
def contract():
    return json.loads((_BACKEND.parent / "contracts" / "custom_events_contract.json").read_text(encoding="utf-8"))


def _check(contract: dict, event_name: str, payload: dict) -> list[str]:
    """Tiny JSON-schema-ish validator (only the constraints the contract uses)."""
    schema = contract["properties"][event_name]
    errors: list[str] = []
    for required in schema.get("required", []):
        if required not in payload:
            errors.append(f"missing required field {required!r}")
    extra = schema.get("additionalProperties", True)
    if not extra:
        allowed = set(schema.get("properties", {}).keys())
        for k in payload:
            if k not in allowed:
                errors.append(f"unexpected field {k!r}")
    for field, sub in schema.get("properties", {}).items():
        if field not in payload:
            continue
        value = payload[field]
        if "const" in sub and value != sub["const"]:
            errors.append(f"{field!r} expected const {sub['const']!r}, got {value!r}")
        if "enum" in sub and value not in sub["enum"]:
            errors.append(f"{field!r} expected one of {sub['enum']!r}, got {value!r}")
        if sub.get("type") == "integer" and not isinstance(value, int):
            errors.append(f"{field!r} expected integer, got {type(value).__name__}")
        if sub.get("type") == "boolean" and not isinstance(value, bool):
            errors.append(f"{field!r} expected boolean, got {type(value).__name__}")
        if sub.get("type") == "string" and not isinstance(value, str):
            errors.append(f"{field!r} expected string, got {type(value).__name__}")
        if sub.get("type") == "array" and not isinstance(value, list):
            errors.append(f"{field!r} expected array, got {type(value).__name__}")
    return errors


def test_task_progress_payload_conforms(contract):
    payload = {
        "type": "task_progress",
        "step": 1,
        "total": 2,
        "status": "in_progress",
    }
    assert _check(contract, "task_progress", payload) == []


def test_verify_result_payload_conforms(contract):
    payload = {
        "type": "verify_result",
        "ok": False,
        "verdict": "issues",
        "routes": [{"route": "/", "ok": False, "status": 200, "console_errors": []}],
        "console_errors_count": 0,
        "screenshot": None,
        "thread_id": "thread-1",
    }
    assert _check(contract, "verify_result", payload) == []


def test_llm_error_payload_conforms(contract):
    payload = {
        "type": "llm_error",
        "error_type": "Quota",
        "reason": "quota",
        "detail": "insufficient_quota",
        "http_status": 429,
        "code": "insufficient",
    }
    assert _check(contract, "llm_error", payload) == []


def test_task_running_payload_conforms(contract):
    payload = {
        "type": "task_running",
        "task_id": "task-1",
        "message": {"role": "assistant", "content": "delegating"},
    }
    assert _check(contract, "task_running", payload) == []


def test_contract_rejects_wrong_type(contract):
    payload = {"type": "not_task_progress", "step": 1, "total": 2, "status": "in_progress"}
    errors = _check(contract, "task_progress", payload)
    assert any("const" in e for e in errors)


def test_contract_rejects_missing_required(contract):
    payload = {"type": "task_progress", "step": 1, "total": 2}
    errors = _check(contract, "task_progress", payload)
    assert any("status" in e for e in errors)
