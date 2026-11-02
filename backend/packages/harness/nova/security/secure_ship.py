"""Secure-Ship: the pre-deploy security gate.

Nova can build an app and hand it over in a single turn. Before that handover
happens, the build is scanned for the things that most often turn a prototype into
an incident: hard-coded secrets, vulnerable dependencies, leaked credentials in
the tree, and (on a deep scan) container and infrastructure findings.

The gate is deliberately boring. It runs the scanners that ship in the
sandbox, turns their output into one small report, and applies a policy:

* ``pass``  -- nothing at or above the warn threshold was found.
* ``warn``  -- medium findings, or a scanner that could not run.
* ``block`` -- critical or high findings, so the delivery must not happen.
* ``error`` -- the sandbox itself was unavailable, so no claim can be made.

Everything here is importable and testable without a sandbox or a network:
the parsers are pure functions, and only :func:`run_gate` and
:func:`toolkit_manifest` touch the sandbox.
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
import time
from collections.abc import Sequence
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime, timezone
from pathlib import Path
from typing import Any

from deerflow.config.paths import get_paths
from deerflow.sandbox import get_sandbox_provider

logger = logging.getLogger(__name__)

#: Where the agent's build lives, inside the sandbox.
WORKSPACE = "/mnt/user-data/workspace"

#: Where scanner artefacts are written, inside the sandbox.
OUTPUTS = "/mnt/user-data/outputs"

#: How long any one scanner may run. The gate must finish while a user waits.
SCANNER_TIMEOUT_S = 120
DEEP_SCANNER_TIMEOUT_S = 240

#: The report the capability module reads back.
REPORT_FILENAME = "SECURE-SHIP.json"
REPORT_MARKDOWN = "SECURE-SHIP.md"

#: Severities, worst first. Anything outside this set is normalised.
SEVERITIES: tuple[str, ...] = ("critical", "high", "medium", "low", "info")

#: The security tooling in the sandbox image, grouped by domain. This mirrors
#: ``scripts/gates/regression-gate.py`` (``SANDBOX_TOOLCHAIN``) and
#: ``docker/sandbox/verify-toolchain.sh``. The gate only *runs* a subset of
#: them; the rest are listed so the product can show the arsenal it ships.
SECURITY_TOOL_DOMAINS: dict[str, tuple[str, ...]] = {
    "network": ("nmap", "masscan", "tshark"),
    "vulnscan": ("nuclei", "trivy", "nikto", "wapiti", "whatweb"),
    "webapp": ("sqlmap", "gobuster", "ffuf", "dirb", "dalfox"),
    "secrets": ("gitleaks", "trufflehog", "ropper", "detect-secrets"),
    "osint": ("subfinder", "httpx", "amass", "dnsrecon", "sherlock", "arjun"),
    "creds": ("hydra", "john", "hashcat", "smbclient", "secretsdump.py"),
    "forensics": ("binwalk", "foremost", "exiftool", "yara", "fls", "sslscan"),
    "wireless": ("aircrack-ng", "bettercap", "wifite"),
    "blueteam": ("suricata", "lynis", "chkrootkit"),
}

#: The number the marketing page publishes: 40 security tools, 9 domains.
SECURITY_TOOL_COUNT = sum(len(tools) for tools in SECURITY_TOOL_DOMAINS.values())

#: The scanners the gate knows how to run, in report order.
KNOWN_SCANNERS: tuple[str, ...] = ("gitleaks", "detect-secrets", "npm-audit", "trivy")

#: Rule ids (or fragments) that turn a gitleaks match into a critical finding.
_CRITICAL_RULES = ("private-key", "private_key", "rsa", "ssh", "pgp", "keystore")
_HIGH_RULES = ("generic-api-key", "api-key", "aws", "github", "gitlab", "slack", "stripe", "token", "secret", "password", "credential")


# ---------------------------------------------------------------------------
# Data model
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Finding:
    """One thing the gate wants a human to know about."""

    scanner: str
    severity: str
    title: str
    path: str | None = None
    line: int | None = None
    detail: str | None = None
    rule: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "scanner": self.scanner,
            "severity": self.severity,
            "title": self.title,
            "path": self.path,
            "line": self.line,
            "detail": self.detail,
            "rule": self.rule,
        }


@dataclass(frozen=True)
class ScannerResult:
    """What one scanner did, and what it found."""

    scanner: str
    status: str  # pass | fail | skipped | error
    summary: str
    findings: tuple[Finding, ...] = ()
    duration_ms: int = 0

    def to_dict(self) -> dict[str, Any]:
        return {
            "scanner": self.scanner,
            "status": self.status,
            "summary": self.summary,
            "findings": [finding.to_dict() for finding in self.findings],
            "duration_ms": self.duration_ms,
        }


@dataclass(frozen=True)
class GatePolicy:
    """What the gate blocks on, and which scanners it runs."""

    name: str = "default"
    block_severities: tuple[str, ...] = ("critical", "high")
    warn_severities: tuple[str, ...] = ("medium",)
    max_findings: int = 0  # 0 means "no cap"; >0 blocks above the cap.
    scanners: tuple[str, ...] = ("gitleaks", "detect-secrets", "npm-audit")
    deep: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "block_severities": list(self.block_severities),
            "warn_severities": list(self.warn_severities),
            "max_findings": self.max_findings,
            "scanners": list(self.scanners),
            "deep": self.deep,
        }


@dataclass(frozen=True)
class GateReport:
    """The gate's verdict and the evidence behind it."""

    thread_id: str
    verdict: str  # pass | warn | block | error
    policy: str
    scanners: tuple[ScannerResult, ...] = ()
    findings: tuple[Finding, ...] = ()
    counts: dict[str, int] = field(default_factory=dict)
    duration_ms: int = 0
    generated_at: str = ""
    reason: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "thread_id": self.thread_id,
            "verdict": self.verdict,
            "policy": self.policy,
            "scanners": [scanner.to_dict() for scanner in self.scanners],
            "findings": [finding.to_dict() for finding in self.findings],
            "counts": dict(self.counts),
            "duration_ms": self.duration_ms,
            "generated_at": self.generated_at,
            "reason": self.reason,
        }


def default_policy() -> GatePolicy:
    """The policy a normal ship uses."""
    return GatePolicy()


def _now() -> str:
    return datetime.now(UTC).isoformat()


def _norm_severity(value: Any, default: str = "medium") -> str:
    """Normalise the many spellings scanners use into the five gate levels."""
    raw = str(value or "").strip().lower()
    if raw in {"moderate"}:
        return "medium"
    if raw in {"informational", "information", "note", "none", "unknown"}:
        return "info" if raw in {"informational", "information", "note"} else default
    if raw in SEVERITIES:
        return raw
    if raw in {"crit", "severe"}:
        return "critical"
    if raw in {"med"}:
        return "medium"
    return default


def severity_counts(findings: Sequence[Finding]) -> dict[str, int]:
    """Count findings by severity, with every severity present.

    The UI renders a fixed set of buckets, so a missing key is less useful
    than ``0``. ``counts["total"]`` is not included; the caller can sum.
    """
    counts = {severity: 0 for severity in SEVERITIES}
    for finding in findings:
        severity = _norm_severity(finding.severity)
        counts[severity] = counts.get(severity, 0) + 1
    return counts


def _finding_label(finding: Finding) -> str:
    """``Possible hard-coded secret in app/config.py`` for a reason line."""
    if finding.path and finding.path not in finding.title:
        return f"{finding.title} in {finding.path}"
    return finding.title


def evaluate(findings: Sequence[Finding], policy: GatePolicy) -> tuple[str, str | None]:
    """Apply *policy* to *findings* and return ``(verdict, reason)``."""
    counts = severity_counts(findings)
    total = sum(counts.values())

    blocking: list[Finding] = [f for f in findings if _norm_severity(f.severity) in policy.block_severities]
    if blocking:
        ordered = sorted(blocking, key=lambda f: SEVERITIES.index(_norm_severity(f.severity)))
        top = ordered[0]
        label = _finding_label(top)
        plural = "" if len(blocking) == 1 else "s"
        return "block", f"{len(blocking)} blocking finding{plural}: {label}"

    if policy.max_findings and total > policy.max_findings:
        return "block", f"{total} findings exceed the policy cap of {policy.max_findings}"

    warning = [f for f in findings if _norm_severity(f.severity) in policy.warn_severities]
    if warning:
        return "warn", f"{len(warning)} finding{'' if len(warning) == 1 else 's'} to review before shipping"

    return "pass", None


# ---------------------------------------------------------------------------
# Parsers -- pure functions, no sandbox, no network
# ---------------------------------------------------------------------------


def _relative_path(raw: Any) -> str | None:
    """A finding's path, relative to the workspace when it points inside it."""
    if raw is None:
        return None
    path = str(raw).strip()
    if not path:
        return None
    for prefix in (WORKSPACE + "/", WORKSPACE):
        if path.startswith(prefix):
            return path[len(prefix) :].lstrip("/") or "."
    return path


def _int_or_none(value: Any) -> int | None:
    try:
        number = int(value)
    except (TypeError, ValueError):
        return None
    return number if number > 0 else None


def _gitleaks_severity(rule: str, description: str) -> str:
    haystack = f"{rule} {description}".lower()
    if any(fragment in haystack for fragment in _CRITICAL_RULES):
        return "critical"
    # A confirmed match from gitleaks is a real secret; high is the floor.
    return "high"


def parse_gitleaks(payload: Any) -> list[Finding]:
    """Parse ``gitleaks dir --report-format json`` output.

    gitleaks 8.21 writes a top-level list; newer releases may wrap it in
    ``{"Findings": [...]}``. Both are accepted, and a malformed body returns
    no findings rather than raising.
    """
    if isinstance(payload, dict):
        entries = payload.get("Findings") or payload.get("findings") or []
    elif isinstance(payload, list):
        entries = payload
    else:
        return []
    if not isinstance(entries, list):
        return []

    findings: list[Finding] = []
    for entry in entries:
        if not isinstance(entry, dict):
            continue
        rule = str(entry.get("RuleID") or entry.get("RuleId") or entry.get("rule") or "").strip()
        description = str(entry.get("Description") or entry.get("description") or "").strip()
        title = description or (f"Secret matched rule {rule}" if rule else "Possible hard-coded secret")
        findings.append(
            Finding(
                scanner="gitleaks",
                severity=_gitleaks_severity(rule, description),
                title=title,
                path=_relative_path(entry.get("File") or entry.get("file")),
                line=_int_or_none(entry.get("StartLine") or entry.get("startLine")),
                detail=(str(entry.get("Match") or "").strip() or None),
                rule=rule or None,
            )
        )
    return findings


def parse_detect_secrets(payload: Any) -> list[Finding]:
    """Parse ``detect-secrets scan`` JSON.

    The scanner reports every occurrence; the gate reports one finding per
    file/type pair so a file with twenty test keys is not twenty blockers.
    """
    if not isinstance(payload, dict):
        return []
    results = payload.get("results")
    if not isinstance(results, dict):
        return []

    findings: list[Finding] = []
    for path, entries in results.items():
        if not isinstance(entries, list):
            continue
        seen: set[str] = set()
        for entry in entries:
            if not isinstance(entry, dict):
                continue
            kind = str(entry.get("type") or entry.get("Type") or "Secret").strip()
            if kind in seen:
                continue
            seen.add(kind)
            severe = kind in {"PrivateKeyDetector", "AWSKeyDetector", "AzureStorageAccountKeyDetector", "GitHubTokenDetector", "SlackDetector"}
            findings.append(
                Finding(
                    scanner="detect-secrets",
                    severity="high" if severe else "medium",
                    title=f"Possible {kind.replace('Detector', '')} secret",
                    path=_relative_path(path),
                    line=_int_or_none(entry.get("line_number") or entry.get("line")),
                    detail=None,
                    rule=kind or None,
                )
            )
    return findings


def parse_npm_audit(payload: Any) -> list[Finding]:
    """Parse ``npm audit --json`` output.

    One finding per vulnerable package. ``via`` can be a list of strings or
    advisory objects; the first useful title is used for the detail.
    """
    if not isinstance(payload, dict):
        return []
    vulnerabilities = payload.get("vulnerabilities")
    if not isinstance(vulnerabilities, dict):
        return []

    findings: list[Finding] = []
    for name, entry in vulnerabilities.items():
        if not isinstance(entry, dict):
            continue
        severity = _norm_severity(entry.get("severity"), default="medium")
        via = entry.get("via")
        detail: str | None = None
        if isinstance(via, list):
            for item in via:
                if isinstance(item, str):
                    detail = item
                    break
                if isinstance(item, dict) and item.get("title"):
                    detail = str(item["title"])
                    break
        title = f"Vulnerable dependency: {name}"
        if detail:
            title = f"{title} ({detail})"
        findings.append(
            Finding(
                scanner="npm-audit",
                severity=severity,
                title=title,
                path="package-lock.json",
                detail=detail,
                rule=str(entry.get("range") or entry.get("fixAvailable") or "") or None,
            )
        )
    return findings


def parse_trivy_fs(payload: Any) -> list[Finding]:
    """Parse ``trivy fs --format json`` output.

    Handles vulnerabilities, secrets and misconfigurations. Entries without a
    severity default to medium, which is what trivy itself uses for
    ``UNKNOWN`` in most reports.
    """
    if not isinstance(payload, dict):
        return []
    results = payload.get("Results")
    if not isinstance(results, list):
        return []

    findings: list[Finding] = []
    for result in results:
        if not isinstance(result, dict):
            continue
        target = _relative_path(result.get("Target"))
        for vuln in result.get("Vulnerabilities") or []:
            if not isinstance(vuln, dict):
                continue
            identifier = str(vuln.get("VulnerabilityID") or vuln.get("ID") or "").strip()
            package = str(vuln.get("PkgName") or "").strip()
            title = str(vuln.get("Title") or "").strip()
            if not title:
                title = f"{identifier} in {package}" if identifier and package else "Vulnerable dependency"
            findings.append(
                Finding(
                    scanner="trivy",
                    severity=_norm_severity(vuln.get("Severity"), default="medium"),
                    title=title,
                    path=target,
                    detail=str(vuln.get("PrimaryURL") or "") or None,
                    rule=identifier or None,
                )
            )
        for secret in result.get("Secrets") or []:
            if not isinstance(secret, dict):
                continue
            findings.append(
                Finding(
                    scanner="trivy",
                    severity=_norm_severity(secret.get("Severity"), default="high"),
                    title=str(secret.get("Title") or "Secret detected"),
                    path=target,
                    line=_int_or_none(secret.get("StartLine")),
                    rule=str(secret.get("RuleID") or "") or None,
                )
            )
        for misconfig in result.get("Misconfigurations") or []:
            if not isinstance(misconfig, dict):
                continue
            findings.append(
                Finding(
                    scanner="trivy",
                    severity=_norm_severity(misconfig.get("Severity"), default="medium"),
                    title=str(misconfig.get("Title") or misconfig.get("ID") or "Misconfiguration"),
                    path=target,
                    detail=str(misconfig.get("Message") or "") or None,
                    rule=str(misconfig.get("ID") or "") or None,
                )
            )
    return findings


#: Parser lookup, keyed by scanner name. Kept here so the runner and the tests
#: share exactly one mapping.
PARSERS = {
    "gitleaks": parse_gitleaks,
    "detect-secrets": parse_detect_secrets,
    "npm-audit": parse_npm_audit,
    "trivy": parse_trivy_fs,
}


# ---------------------------------------------------------------------------
# Sandbox execution
# ---------------------------------------------------------------------------


def _failure_report(thread_id: str, policy: GatePolicy, reason: str, started: float) -> GateReport:
    """A report that says, honestly, that the gate did not run."""
    return GateReport(
        thread_id=thread_id,
        verdict="error",
        policy=policy.name,
        scanners=(),
        findings=(),
        counts=severity_counts([]),
        duration_ms=int((time.monotonic() - started) * 1000),
        generated_at=_now(),
        reason=reason,
    )


def _exit_code(output: str) -> int | None:
    match = re.search(r"__EXIT__=(-?\d+)", output)
    return int(match.group(1)) if match else None


def _probe_scanners(sandbox: Any, names: Sequence[str]) -> dict[str, bool]:
    """Which scanners actually exist in the sandbox."""
    checks = " ".join(names)
    command = f'for t in {checks}; do if command -v "$t" >/dev/null 2>&1; then echo "$t=1"; else echo "$t=0"; fi; done; echo __EXIT__=0'
    try:
        output = sandbox.execute_command(command) or ""
    except Exception:  # noqa: BLE001 - an unprobeable scanner is "missing"
        logger.debug("Secure-Ship: scanner probe failed", exc_info=True)
        return {name: False for name in names}
    found = {name: False for name in names}
    for line in output.splitlines():
        name, _, value = line.strip().partition("=")
        if name in found:
            found[name] = value.strip() == "1"
    return found


def _read_artifact(sandbox: Any, filename: str, user_id: str | None, thread_id: str) -> Any:
    """Read a scanner's JSON report from the sandbox, or the host mount.

    The outputs directory is bind-mounted, so the host read is both faster and
    independent of what the sandbox RPC supports. It is tried second so a
    remote sandbox without a shared mount still works.
    """
    sandbox_path = f"{OUTPUTS}/{filename}"
    raw: str | None = None
    try:
        content = sandbox.read_file(sandbox_path)
        if isinstance(content, str) and not content.startswith("Error:") and content.strip():
            raw = content
    except Exception:  # noqa: BLE001 - fall through to the host mount
        logger.debug("Secure-Ship: sandbox read failed for %s", sandbox_path, exc_info=True)
    if raw is None:
        try:
            host_path = get_paths().sandbox_outputs_dir(thread_id, user_id=user_id) / filename
            raw = host_path.read_text(encoding="utf-8")
        except (OSError, ValueError):
            return None
    try:
        return json.loads(raw)
    except (TypeError, ValueError):
        logger.debug("Secure-Ship: %s is not valid JSON", filename)
        return None


def _run_scanner(
    sandbox: Any,
    scanner: str,
    *,
    thread_id: str,
    user_id: str | None,
    deep: bool,
) -> ScannerResult:
    """Run one scanner and turn its output into a :class:`ScannerResult`."""
    started = time.monotonic()
    filename = f"secure-ship-{scanner.replace('-', '_')}.json"
    output_path = f"{OUTPUTS}/{filename}"
    timeout = DEEP_SCANNER_TIMEOUT_S if scanner == "trivy" else SCANNER_TIMEOUT_S

    if scanner == "npm-audit":
        command = (
            f"cd {WORKSPACE} 2>/dev/null || exit 1; "
            f"if [ ! -f package.json ]; then echo __EXIT__=44; exit 0; fi; "
            f"if [ ! -f package-lock.json ] && [ ! -f npm-shrinkwrap.json ] && "
            f"[ ! -f yarn.lock ] && [ ! -f pnpm-lock.yaml ]; then echo __EXIT__=44; exit 0; fi; "
            f"timeout --signal=KILL {timeout} npm audit --json > {output_path} 2>/dev/null; "
            "echo __EXIT__=$?"
        )
    elif scanner == "detect-secrets":
        command = f"mkdir -p {OUTPUTS}; timeout --signal=KILL {timeout} detect-secrets scan --all-files {WORKSPACE} > {output_path} 2>/dev/null; echo __EXIT__=$?"
    elif scanner == "gitleaks":
        command = f"mkdir -p {OUTPUTS}; timeout --signal=KILL {timeout} gitleaks dir {WORKSPACE} --redact --report-format json --report-path {output_path} --exit-code 0 --no-banner >/dev/null 2>&1; echo __EXIT__=$?"
    elif scanner == "trivy":
        command = f"mkdir -p {OUTPUTS}; timeout --signal=KILL {timeout} trivy fs --quiet --format json --severity HIGH,CRITICAL --scanners vuln,secret,misconfig --output {output_path} {WORKSPACE} >/dev/null 2>&1; echo __EXIT__=$?"
    else:
        return ScannerResult(
            scanner=scanner,
            status="skipped",
            summary="unknown scanner",
            duration_ms=0,
        )

    try:
        output = sandbox.execute_command(command) or ""
    except Exception as exc:  # noqa: BLE001 - one broken scanner is not a gate failure
        logger.warning("Secure-Ship: %s could not run: %s", scanner, exc)
        return ScannerResult(
            scanner=scanner,
            status="error",
            summary=f"scanner crashed: {exc.__class__.__name__}",
            duration_ms=int((time.monotonic() - started) * 1000),
        )

    code = _exit_code(output)
    duration = int((time.monotonic() - started) * 1000)

    if scanner == "npm-audit" and code == 44:
        return ScannerResult(
            scanner=scanner,
            status="skipped",
            summary="no package.json in the build",
            duration_ms=duration,
        )
    if code in {124, 137}:
        return ScannerResult(
            scanner=scanner,
            status="error",
            summary=f"timed out after {timeout}s",
            duration_ms=duration,
        )

    payload = _read_artifact(sandbox, filename, user_id, thread_id)
    if payload is None:
        # A missing report is not automatically a failure: gitleaks and
        # detect-secrets write nothing when there is nothing to report.
        if code in {0, None}:
            return ScannerResult(
                scanner=scanner,
                status="pass",
                summary="no findings",
                duration_ms=duration,
            )
        return ScannerResult(
            scanner=scanner,
            status="error",
            summary=f"scanner exited with code {code} and wrote no report",
            duration_ms=duration,
        )

    findings = tuple(PARSERS[scanner](payload))
    if not findings:
        return ScannerResult(
            scanner=scanner,
            status="pass",
            summary="no findings",
            duration_ms=duration,
        )
    return ScannerResult(
        scanner=scanner,
        status="fail",
        summary=f"{len(findings)} finding{'' if len(findings) == 1 else 's'}",
        findings=findings,
        duration_ms=duration,
    )


def _write_report(report: GateReport, thread_id: str, user_id: str | None) -> None:
    """Persist the report next to the build. Best effort: never fail a gate."""
    try:
        workspace = get_paths().sandbox_work_dir(thread_id, user_id=user_id)
        workspace.mkdir(parents=True, exist_ok=True)
        payload = json.dumps(report.to_dict(), indent=2, sort_keys=True)
        (workspace / REPORT_FILENAME).write_text(payload + "\n", encoding="utf-8")
        (workspace / REPORT_MARKDOWN).write_text(_markdown(report), encoding="utf-8")
        outputs = get_paths().sandbox_outputs_dir(thread_id, user_id=user_id)
        outputs.mkdir(parents=True, exist_ok=True)
        (outputs / REPORT_FILENAME).write_text(payload + "\n", encoding="utf-8")
    except OSError as exc:
        logger.debug("Secure-Ship: could not persist the report: %s", exc)


def _markdown(report: GateReport) -> str:
    """A short human summary, in the voice of the rest of the product."""
    lines = [
        f"# Secure-Ship — {report.verdict.upper()}",
        "",
        f"Scanned thread `{report.thread_id}` at {report.generated_at}.",
        "",
    ]
    if report.reason:
        lines += [report.reason, ""]
    if report.findings:
        lines.append("## Findings")
        lines.append("")
        for finding in report.findings:
            location = finding.path or ""
            if finding.line:
                location = f"{location}:{finding.line}" if location else f"line {finding.line}"
            suffix = f" — {location}" if location else ""
            lines.append(f"- **{finding.severity}** · {finding.title}{suffix} (`{finding.scanner}`)")
        lines.append("")
    else:
        lines += ["No findings.", ""]
    lines.append("## Scanners")
    lines.append("")
    for scanner in report.scanners:
        lines.append(f"- `{scanner.scanner}` — {scanner.status}: {scanner.summary} ({scanner.duration_ms} ms)")
    lines.append("")
    return "\n".join(lines)


def _run_gate_sync(
    thread_id: str,
    *,
    user_id: str | None,
    policy: GatePolicy,
    deep: bool,
) -> GateReport:
    """The blocking half of :func:`run_gate`; runs in a worker thread."""
    started = time.monotonic()
    try:
        provider = get_sandbox_provider()
        sandbox_id = provider.acquire(thread_id)
        sandbox = provider.get(sandbox_id)
        if sandbox is None:
            return _failure_report(thread_id, policy, "the thread's sandbox is not available", started)
        mark_active = getattr(provider, "mark_active", None)
        if callable(mark_active):
            try:
                mark_active(thread_id)
            except Exception:  # noqa: BLE001 - activity tracking is best effort
                pass
    except Exception as exc:  # noqa: BLE001 - no sandbox means "cannot decide"
        logger.warning("Secure-Ship: sandbox unavailable for thread=%s: %s", thread_id, exc)
        return _failure_report(thread_id, policy, f"sandbox unavailable: {exc}", started)

    effective = policy
    if deep and not effective.deep:
        scanners = tuple(dict.fromkeys((*effective.scanners, "trivy")))
        effective = replace(effective, deep=True, scanners=scanners)

    available = _probe_scanners(sandbox, effective.scanners)
    results: list[ScannerResult] = []
    for scanner in effective.scanners:
        if scanner not in PARSERS:
            results.append(ScannerResult(scanner=scanner, status="skipped", summary="unknown scanner"))
            continue
        if not available.get(scanner, False):
            results.append(
                ScannerResult(
                    scanner=scanner,
                    status="skipped",
                    summary="scanner unavailable in this sandbox",
                )
            )
            continue
        results.append(_run_scanner(sandbox, scanner, thread_id=thread_id, user_id=user_id, deep=deep))

    findings = tuple(finding for result in results for finding in result.findings)
    verdict, reason = evaluate(findings, effective)
    # A scanner that crashed is not a clean bill of health. It does not block
    # (the gate is fail-open), but it does keep the verdict at "warn" or worse
    # so the report never claims more than it knows.
    if verdict == "pass" and any(result.status == "error" for result in results):
        verdict = "warn"
        failed = ", ".join(result.scanner for result in results if result.status == "error")
        reason = f"some scanners could not run: {failed}"

    report = GateReport(
        thread_id=thread_id,
        verdict=verdict,
        policy=effective.name,
        scanners=tuple(results),
        findings=findings,
        counts=severity_counts(findings),
        duration_ms=int((time.monotonic() - started) * 1000),
        generated_at=_now(),
        reason=reason,
    )
    _write_report(report, thread_id, user_id)
    return report


async def run_gate(
    thread_id: str,
    *,
    user_id: str | None = None,
    policy: GatePolicy | None = None,
    deep: bool = False,
) -> GateReport:
    """Scan a thread's build and return the gate's report.

    Never raises for scanner problems: a missing scanner is ``skipped``, a
    crashed scanner is ``error``, and an unavailable sandbox produces a
    ``verdict="error"`` report with a reason.
    """
    effective = policy or default_policy()
    started = time.monotonic()
    try:
        return await asyncio.to_thread(
            _run_gate_sync,
            thread_id,
            user_id=user_id,
            policy=effective,
            deep=deep,
        )
    except Exception as exc:  # noqa: BLE001 - the contract is "a report, never a raise"
        logger.exception("Secure-Ship: unexpected gate failure for thread=%s", thread_id)
        return _failure_report(thread_id, effective, f"gate failed: {exc}", started)


def toolkit_manifest(thread_id: str | None = None) -> dict[str, Any]:
    """The security tooling available, grouped by domain.

    With a thread id the sandbox's ``/etc/nova-sandbox.json`` is read for live
    versions; without one the static inventory is returned. The static
    inventory is the source of truth for the count (40).
    """
    groups = {domain: list(tools) for domain, tools in SECURITY_TOOL_DOMAINS.items()}
    manifest: dict[str, Any] = {
        "total": SECURITY_TOOL_COUNT,
        "groups": groups,
        "version": None,
    }
    if not thread_id:
        return manifest

    try:
        provider = get_sandbox_provider()
        sandbox_id = provider.acquire(thread_id)
        sandbox = provider.get(sandbox_id)
        if sandbox is None:
            return manifest
        raw = sandbox.read_file("/etc/nova-sandbox.json")
        if not isinstance(raw, str) or raw.startswith("Error:"):
            return manifest
        payload = json.loads(raw)
        if not isinstance(payload, dict):
            return manifest
        manifest["version"] = payload.get("version") or payload.get("build_id")
        manifest["build_id"] = payload.get("build_id")
        manifest["built_at"] = payload.get("built_at")
        toolchain = payload.get("toolchain")
        if isinstance(toolchain, dict):
            manifest["versions"] = {str(name): str(value) for name, value in toolchain.items()}
    except Exception:  # noqa: BLE001 - the static inventory is still the answer
        logger.debug("Secure-Ship: live toolkit manifest unavailable", exc_info=True)
    return manifest


__all__ = [
    "Finding",
    "ScannerResult",
    "GatePolicy",
    "GateReport",
    "SECURITY_TOOL_DOMAINS",
    "SECURITY_TOOL_COUNT",
    "default_policy",
    "evaluate",
    "parse_gitleaks",
    "parse_detect_secrets",
    "parse_npm_audit",
    "parse_trivy_fs",
    "severity_counts",
    "run_gate",
    "toolkit_manifest",
]
