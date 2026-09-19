"""Wire-contract test for the SSE ``custom`` event payloads.

Pinned by the 2026-08-14 audit: the subagent_status family already has its own
contract fixture; the other custom events did not. This test loads
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


# ---------------------------------------------------------------------------
# Existing events
# ---------------------------------------------------------------------------


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


# ---------------------------------------------------------------------------
# safety_termination — emitted by safety_finish_reason_middleware.py
# ---------------------------------------------------------------------------


def test_safety_termination_payload_conforms(contract):
    payload = {
        "type": "safety_termination",
        "detector": "OpenAICompatibleContentFilterDetector",
        "reason_field": "finish_reason",
        "reason_value": "content_filter",
        "suppressed_tool_call_count": 2,
        "suppressed_tool_call_names": ["bash", "write_file"],
        "thread_id": "thread-1",
    }
    assert _check(contract, "safety_termination", payload) == []


def test_safety_termination_without_optional_thread_id(contract):
    payload = {
        "type": "safety_termination",
        "detector": "OpenAICompatibleContentFilterDetector",
        "reason_field": "finish_reason",
        "reason_value": "content_filter",
        "suppressed_tool_call_count": 0,
        "suppressed_tool_call_names": [],
    }
    assert _check(contract, "safety_termination", payload) == []


# ---------------------------------------------------------------------------
# llm_retry — emitted by llm_error_handling_middleware.py
# ---------------------------------------------------------------------------


def test_llm_retry_payload_conforms(contract):
    payload = {
        "type": "llm_retry",
        "attempt": 1,
        "max_attempts": 3,
        "wait_ms": 1000,
        "reason": "rate_limit",
        "message": "Rate limited, retrying in 1s (attempt 1/3)",
    }
    assert _check(contract, "llm_retry", payload) == []


def test_llm_retry_final_attempt(contract):
    payload = {
        "type": "llm_retry",
        "attempt": 3,
        "max_attempts": 3,
        "wait_ms": 0,
        "reason": "server_error",
        "message": "Server error, retrying (attempt 3/3)",
    }
    assert _check(contract, "llm_retry", payload) == []


# ---------------------------------------------------------------------------
# task_started — emitted by tools/builtins/task_tool.py
# ---------------------------------------------------------------------------


def test_task_started_payload_conforms(contract):
    payload = {
        "type": "task_started",
        "task_id": "task-abc",
        "description": "Build a hello world app",
        "todo_indexes": [0],
    }
    assert _check(contract, "task_started", payload) == []


def test_task_started_empty_todo_indexes(contract):
    payload = {
        "type": "task_started",
        "task_id": "task-xyz",
        "description": "Run tests",
        "todo_indexes": [],
    }
    assert _check(contract, "task_started", payload) == []


# ---------------------------------------------------------------------------
# task_completed — emitted by tools/builtins/task_tool.py
# ---------------------------------------------------------------------------


def test_task_completed_payload_conforms(contract):
    payload = {
        "type": "task_completed",
        "task_id": "task-abc",
        "result": "Build succeeded",
        "usage": {"input_tokens": 500, "output_tokens": 200},
        "todo_indexes": [0],
    }
    assert _check(contract, "task_completed", payload) == []


def test_task_completed_empty_usage(contract):
    payload = {
        "type": "task_completed",
        "task_id": "task-abc",
        "result": "Done",
        "usage": {},
        "todo_indexes": [],
    }
    assert _check(contract, "task_completed", payload) == []


# ---------------------------------------------------------------------------
# task_failed — emitted by tools/builtins/task_tool.py
# ---------------------------------------------------------------------------


def test_task_failed_payload_conforms(contract):
    payload = {
        "type": "task_failed",
        "task_id": "task-abc",
        "error": "Task disappeared from background tasks",
        "usage": {"input_tokens": 100, "output_tokens": 50},
        "todo_indexes": [1],
    }
    assert _check(contract, "task_failed", payload) == []


def test_task_failed_without_optional_fields(contract):
    payload = {
        "type": "task_failed",
        "task_id": "task-abc",
        "error": "Task disappeared from background tasks",
    }
    assert _check(contract, "task_failed", payload) == []


# ---------------------------------------------------------------------------
# task_timed_out — emitted by tools/builtins/task_tool.py
# ---------------------------------------------------------------------------


def test_task_timed_out_payload_conforms(contract):
    payload = {
        "type": "task_timed_out",
        "task_id": "task-abc",
        "usage": {"input_tokens": 300, "output_tokens": 100},
        "todo_indexes": [0],
    }
    assert _check(contract, "task_timed_out", payload) == []


def test_task_timed_out_with_error(contract):
    payload = {
        "type": "task_timed_out",
        "task_id": "task-abc",
        "error": "Execution timed out after 1800s",
        "usage": {},
        "todo_indexes": [],
    }
    assert _check(contract, "task_timed_out", payload) == []


# ---------------------------------------------------------------------------
# task_activity — emitted by observe_adjust_middleware.py
# ---------------------------------------------------------------------------


def test_task_activity_payload_conforms(contract):
    payload = {
        "type": "task_activity",
        "tool_call_id": "call-123",
        "status": "done",
    }
    assert _check(contract, "task_activity", payload) == []


# ---------------------------------------------------------------------------
# Generic contract rejection tests
# ---------------------------------------------------------------------------


def test_contract_rejects_wrong_type(contract):
    payload = {"type": "not_task_progress", "step": 1, "total": 2, "status": "in_progress"}
    errors = _check(contract, "task_progress", payload)
    assert any("const" in e for e in errors)


def test_contract_rejects_missing_required(contract):
    payload = {"type": "task_progress", "step": 1, "total": 2}
    errors = _check(contract, "task_progress", payload)
    assert any("status" in e for e in errors)


def test_contract_rejects_extra_field(contract):
    payload = {
        "type": "safety_termination",
        "detector": "test",
        "reason_field": "test",
        "reason_value": "test",
        "suppressed_tool_call_count": 0,
        "suppressed_tool_call_names": [],
        "bogus_field": "should fail",
    }
    errors = _check(contract, "safety_termination", payload)
    assert any("bogus_field" in e for e in errors)


def test_contract_rejects_wrong_type_on_llm_retry(contract):
    payload = {
        "type": "not_llm_retry",
        "attempt": 1,
        "max_attempts": 3,
        "wait_ms": 0,
        "reason": "x",
        "message": "x",
    }
    errors = _check(contract, "llm_retry", payload)
    assert any("const" in e for e in errors)


def test_contract_rejects_missing_required_on_task_started(contract):
    payload = {"type": "task_started", "task_id": "x"}
    errors = _check(contract, "task_started", payload)
    assert any("description" in e for e in errors)


def test_acp_update_payload_conforms(contract):
    """P6: live transcript of an ACP agent (invoke_acp_agent_tool)."""
    payload = {"type": "acp_update", "agent": "claude_code", "session_id": "s-1", "kind": "text", "delta": "Hello"}
    assert _check(contract, "acp_update", payload) == []
    assert _check(contract, "acp_update", {**payload, "kind": "thought"}) != []
    assert _check(contract, "acp_update", {**payload, "extra": 1}) != []
