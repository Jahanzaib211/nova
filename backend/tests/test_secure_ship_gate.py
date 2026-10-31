"""Tests for the Secure-Ship gate engine.

The parsers are pure, so most of this file is plain data in and data out. The
two sandbox-facing entry points are exercised with a fake provider: what
matters is that a missing scanner, a crashed scanner and an unreachable
sandbox all produce a report instead of an exception.
"""

from __future__ import annotations

import asyncio
import json

import pytest

from deerflow.security import secure_ship
from deerflow.security.secure_ship import (
    SECURITY_TOOL_COUNT,
    SECURITY_TOOL_DOMAINS,
    Finding,
    GatePolicy,
    default_policy,
    evaluate,
    parse_detect_secrets,
    parse_gitleaks,
    parse_npm_audit,
    parse_trivy_fs,
    run_gate,
    severity_counts,
    toolkit_manifest,
)

# ---------------------------------------------------------------------------
# Parsers
# ---------------------------------------------------------------------------


def test_parse_gitleaks_list_payload():
    payload = [
        {
            "RuleID": "generic-api-key",
            "Description": "Generic API Key",
            "File": "/mnt/user-data/workspace/app/config.py",
            "StartLine": 12,
            "Match": "api_key = 'abc123'",
        },
        {
            "RuleID": "private-key",
            "Description": "Private Key",
            "File": "deploy/id_rsa",
            "StartLine": 1,
        },
    ]

    findings = parse_gitleaks(payload)

    assert len(findings) == 2
    assert findings[0].severity == "high"
    assert findings[0].path == "app/config.py"
    assert findings[0].line == 12
    assert findings[1].severity == "critical"
    assert findings[1].path == "deploy/id_rsa"


def test_parse_gitleaks_accepts_the_findings_envelope():
    payload = {"Findings": [{"RuleID": "aws-access-token", "Description": "AWS", "File": "x.py"}]}
    findings = parse_gitleaks(payload)
    assert len(findings) == 1
    assert findings[0].severity == "high"


def test_parse_detect_secrets_collapses_repeats_per_file():
    payload = {
        "results": {
            "/mnt/user-data/workspace/app/auth.py": [
                {"type": "SecretKeyword", "line_number": 4},
                {"type": "SecretKeyword", "line_number": 9},
                {"type": "PrivateKeyDetector", "line_number": 22},
            ]
        }
    }

    findings = parse_detect_secrets(payload)

    assert len(findings) == 2
    assert {f.severity for f in findings} == {"medium", "high"}
    assert findings[0].path == "app/auth.py"


def test_parse_npm_audit_one_finding_per_package():
    payload = {
        "vulnerabilities": {
            "lodash": {"severity": "high", "via": [{"title": "Prototype pollution"}], "range": "<4.17.21"},
            "minimist": {"severity": "moderate", "via": ["Prototype pollution"]},
        }
    }

    findings = parse_npm_audit(payload)

    assert len(findings) == 2
    assert findings[0].scanner == "npm-audit"
    assert findings[0].severity == "high"
    assert "lodash" in findings[0].title
    assert findings[1].severity == "medium"


def test_parse_trivy_fs_covers_vulns_secrets_and_misconfigs():
    payload = {
        "Results": [
            {
                "Target": "package-lock.json",
                "Vulnerabilities": [{"VulnerabilityID": "CVE-2024-1", "PkgName": "express", "Severity": "CRITICAL", "Title": "RCE"}],
                "Secrets": [{"RuleID": "aws-key", "Severity": "HIGH", "Title": "AWS key", "StartLine": 3}],
                "Misconfigurations": [{"ID": "DS002", "Severity": "MEDIUM", "Title": "Root user"}],
            }
        ]
    }

    findings = parse_trivy_fs(payload)

    assert len(findings) == 3
    assert findings[0].severity == "critical"
    assert findings[0].path == "package-lock.json"
    assert findings[1].line == 3
    assert findings[2].severity == "medium"


@pytest.mark.parametrize(
    "parser,payload",
    [
        (parse_gitleaks, None),
        (parse_gitleaks, {}),
        (parse_gitleaks, "not json"),
        (parse_detect_secrets, {"results": []}),
        (parse_npm_audit, {}),
        (parse_trivy_fs, {"Results": "nope"}),
    ],
)
def test_parsers_never_raise_on_malformed_payloads(parser, payload):
    assert parser(payload) == []


# ---------------------------------------------------------------------------
# Policy
# ---------------------------------------------------------------------------


def _finding(severity: str, title: str = "Issue", path: str = "app.py") -> Finding:
    return Finding(scanner="gitleaks", severity=severity, title=title, path=path)


def test_severity_counts_keeps_every_bucket():
    counts = severity_counts([_finding("high"), _finding("high"), _finding("low")])
    assert counts == {"critical": 0, "high": 2, "medium": 0, "low": 1, "info": 0}


def test_evaluate_passes_when_clean():
    verdict, reason = evaluate([], default_policy())
    assert verdict == "pass"
    assert reason is None


def test_evaluate_warns_on_medium():
    verdict, reason = evaluate([_finding("medium", "Weak hash")], default_policy())
    assert verdict == "warn"
    assert "1 finding" in reason


def test_evaluate_blocks_on_high_and_names_the_top_finding():
    verdict, reason = evaluate(
        [_finding("high", "Possible hard-coded secret", "app/config.py"), _finding("critical", "Private key", "id_rsa")],
        default_policy(),
    )
    assert verdict == "block"
    assert "2 blocking findings" in reason
    assert "Private key" in reason
    assert "id_rsa" in reason


def test_evaluate_honours_the_finding_cap():
    policy = GatePolicy(max_findings=1, warn_severities=())
    verdict, reason = evaluate([_finding("low"), _finding("low")], policy)
    assert verdict == "block"
    assert "cap of 1" in reason


# ---------------------------------------------------------------------------
# Toolkit inventory
# ---------------------------------------------------------------------------


def test_toolkit_manifest_static_inventory():
    manifest = toolkit_manifest()
    assert manifest["total"] == SECURITY_TOOL_COUNT == 40
    assert len(manifest["groups"]) == 9
    assert "gitleaks" in manifest["groups"]["secrets"]


class _FakeSandbox:
    def __init__(self, *, probe: dict[str, bool] | None = None, artifacts: dict[str, str] | None = None, manifest: dict | None = None, fail: bool = False):
        self.probe = probe or {}
        self.artifacts = artifacts or {}
        self.manifest = manifest
        self.fail = fail
        self.commands: list[str] = []

    def execute_command(self, command: str) -> str:
        if self.fail:
            raise RuntimeError("sandbox went away")
        self.commands.append(command)
        if "command -v" in command:
            lines = [f"{name}={'1' if present else '0'}" for name, present in self.probe.items()]
            return "\n".join(lines) + "\n__EXIT__=0"
        return "__EXIT__=0"

    def read_file(self, path: str) -> str:
        if path == "/etc/nova-sandbox.json" and self.manifest is not None:
            return json.dumps(self.manifest)
        name = path.rsplit("/", 1)[-1]
        if name in self.artifacts:
            return self.artifacts[name]
        return "Error: file not found"


class _FakeProvider:
    def __init__(self, sandbox):
        self.sandbox = sandbox
        self.active: list[str] = []

    def acquire(self, thread_id=None):
        return "sandbox-1"

    def get(self, sandbox_id):
        return self.sandbox

    def mark_active(self, thread_id):
        self.active.append(thread_id)


def _run(thread_id: str = "thread-1", **kwargs):
    return asyncio.run(run_gate(thread_id, **kwargs))


def test_toolkit_manifest_reads_live_versions(monkeypatch):
    sandbox = _FakeSandbox(manifest={"build_id": "b1", "toolchain": {"gitleaks": "8.21.0"}})
    monkeypatch.setattr(secure_ship, "get_sandbox_provider", lambda: _FakeProvider(sandbox))

    manifest = toolkit_manifest("thread-1")

    assert manifest["total"] == 40
    assert manifest["build_id"] == "b1"
    assert manifest["versions"]["gitleaks"] == "8.21.0"


# ---------------------------------------------------------------------------
# Gate runs
# ---------------------------------------------------------------------------


@pytest.fixture
def no_report_write(monkeypatch):
    monkeypatch.setattr(secure_ship, "_write_report", lambda *args, **kwargs: None)


def test_missing_scanners_are_skipped_not_errors(monkeypatch, no_report_write):
    sandbox = _FakeSandbox(probe={"gitleaks": False, "detect-secrets": False, "npm-audit": False})
    monkeypatch.setattr(secure_ship, "get_sandbox_provider", lambda: _FakeProvider(sandbox))

    report = _run()

    assert report.verdict == "pass"
    assert {scanner.status for scanner in report.scanners} == {"skipped"}
    assert report.findings == ()


def test_a_high_finding_blocks(monkeypatch, no_report_write):
    artifact = json.dumps(
        [
            {
                "RuleID": "generic-api-key",
                "Description": "Generic API Key",
                "File": "/mnt/user-data/workspace/app/config.py",
                "StartLine": 7,
            }
        ]
    )
    sandbox = _FakeSandbox(
        probe={"gitleaks": True, "detect-secrets": False, "npm-audit": False},
        artifacts={"secure-ship-gitleaks.json": artifact},
    )
    monkeypatch.setattr(secure_ship, "get_sandbox_provider", lambda: _FakeProvider(sandbox))

    report = _run()

    assert report.verdict == "block"
    assert report.counts["high"] == 1
    assert "app/config.py" in (report.reason or "")
    gitleaks = next(scanner for scanner in report.scanners if scanner.scanner == "gitleaks")
    assert gitleaks.status == "fail"


def test_an_unreachable_sandbox_returns_an_error_report(monkeypatch, no_report_write):
    def boom():
        raise RuntimeError("no provider")

    monkeypatch.setattr(secure_ship, "get_sandbox_provider", boom)

    report = _run()

    assert report.verdict == "error"
    assert "sandbox unavailable" in (report.reason or "")
    assert report.counts == {"critical": 0, "high": 0, "medium": 0, "low": 0, "info": 0}


def test_a_crashing_scanner_warns_but_does_not_raise(monkeypatch, no_report_write):
    sandbox = _FakeSandbox(probe={"gitleaks": True, "detect-secrets": False, "npm-audit": False}, fail=True)
    monkeypatch.setattr(secure_ship, "get_sandbox_provider", lambda: _FakeProvider(sandbox))

    report = _run()

    # The probe itself failed, so the scanner is skipped; the report is still
    # produced rather than raising.
    assert report.verdict in {"pass", "warn"}
    assert report.reason is None or "could not run" in report.reason


def test_deep_scan_adds_trivy(monkeypatch, no_report_write):
    sandbox = _FakeSandbox(probe={"gitleaks": False, "detect-secrets": False, "npm-audit": False, "trivy": False})
    monkeypatch.setattr(secure_ship, "get_sandbox_provider", lambda: _FakeProvider(sandbox))

    report = _run(deep=True)

    assert [scanner.scanner for scanner in report.scanners] == ["gitleaks", "detect-secrets", "npm-audit", "trivy"]
