"""Unit tests for scripts/gates/capabilities-gate.py (capabilities.json producer)."""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path

SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "gates" / "capabilities-gate.py"
spec = importlib.util.spec_from_file_location("capabilities_gate", SCRIPT)
assert spec and spec.loader
gate = importlib.util.module_from_spec(spec)
spec.loader.exec_module(gate)


def _ops(*, healthy=True, ops=("jobs.list", "jobs.get")):
    return {
        "snapshot": {"version": 1, "modules": [{"id": "jobs", "title": "Jobs", "flag": "jobs", "operations": list(ops)}], "operations": [{"name": n, "kind": "read", "module": "jobs", "input_schema": {}, "output_schema": {}} for n in ops]},
        "status": {"jobs": {"configured": True, "healthy": healthy, "detail": "1 worker" if healthy else "no worker heartbeat"}},
        "flags": {"jobs": True, "runtimes": True},
    }


def _baseline(*ops):
    return {"version": 1, "operations": [{"name": n, "kind": "read", "module": "jobs", "input_schema": {}, "output_schema": {}} for n in ops]}


def _runtimes(*, enabled=True, ready=True):
    return {
        "enabled": enabled,
        "default": "native",
        "runtimes": [
            {"id": "native", "kind": "native", "binary_on_path": True, "accounts": [{"id": "configured-models", "available": True}]},
            {"id": "claude_code", "kind": "acp", "binary_on_path": ready, "accounts": [{"id": "claude-login", "available": ready}]},
        ],
    }


def test_green_when_everything_matches():
    report = gate.build_report(_ops(), _baseline("jobs.list", "jobs.get"), _runtimes(), mcp_status=401)
    assert report["overall"] == "green" and report["ok"] is True
    assert {c["name"] for c in report["checks"]} == {"modules", "contract", "runtimes", "mcp_server"}


def test_red_when_the_live_registry_lost_a_contracted_operation():
    report = gate.build_report(_ops(ops=("jobs.list",)), _baseline("jobs.list", "jobs.get"), _runtimes(), mcp_status=401)
    contract = next(c for c in report["checks"] if c["name"] == "contract")
    assert contract["status"] == "red" and "jobs.get" in contract["detail"]
    assert report["ok"] is False


def test_yellow_when_live_has_operations_the_baseline_lacks():
    report = gate.build_report(_ops(ops=("jobs.list", "jobs.get", "jobs.new")), _baseline("jobs.list", "jobs.get"), _runtimes(), mcp_status=401)
    contract = next(c for c in report["checks"] if c["name"] == "contract")
    assert contract["status"] == "yellow" and "refresh" in contract["detail"]


def test_yellow_on_unhealthy_configured_module_and_no_ready_acp_runtime():
    report = gate.build_report(_ops(healthy=False), _baseline("jobs.list", "jobs.get"), _runtimes(ready=False), mcp_status=401)
    by = {c["name"]: c for c in report["checks"]}
    assert by["modules"]["status"] == "yellow" and "jobs" in by["modules"]["detail"]
    assert by["runtimes"]["status"] == "yellow"
    assert report["overall"] == "yellow"


def test_runtimes_disabled_is_informational_not_a_warning():
    report = gate.build_report(_ops(), _baseline("jobs.list", "jobs.get"), _runtimes(enabled=False), mcp_status=401)
    assert next(c for c in report["checks"] if c["name"] == "runtimes")["status"] == "green"


def test_mcp_server_not_started_is_red():
    report = gate.build_report(_ops(), _baseline("jobs.list", "jobs.get"), _runtimes(), mcp_status=503)
    assert next(c for c in report["checks"] if c["name"] == "mcp_server")["status"] == "red"


def test_unreachable_gateway_is_red_and_still_a_document():
    report = gate.build_report(None, _baseline("jobs.list"), None, mcp_status=None, error="connection refused")
    assert report["overall"] == "red" and report["gate"] == "capabilities"
    assert json.dumps(report)
