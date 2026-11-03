#!/usr/bin/env python3
"""Capabilities gate: writes ~/.nova/gates/capabilities.json.

What it watches (P11–P12):

- **modules** — every capability module's live status from
  ``GET /api/capabilities/ops``; a configured-but-unhealthy module is yellow.
- **contract** — the live registry snapshot against
  ``contracts/capabilities.baseline.json``: an operation the contract
  promises but the gateway no longer serves is red (the UI client and the
  MCP tool list are generated from that contract); operations the gateway
  has that the contract lacks are yellow ("refresh the baseline").
- **runtimes** — ``runtimes.list``: when runtimes are enabled, at least one
  ACP runtime (Claude Code / OpenClaw) should be ready (binary on PATH and an
  account available); otherwise yellow. Disabled is informational.
- **mcp_server** — ``/api/mcp/nova`` answers 401 (bearer required) when the
  server is up; 503 means its session manager never started.

    python3 scripts/gates/capabilities-gate.py            # write + summary
    python3 scripts/gates/capabilities-gate.py --json     # also echo the document
"""

from __future__ import annotations

import argparse
import json
import os
import secrets
import sys
import tempfile
import time
import urllib.error
import urllib.request
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
GREEN, YELLOW, RED = "green", "yellow", "red"
BASELINE = REPO_ROOT / "contracts" / "capabilities.baseline.json"


def check(name: str, status: str, detail: str, **extra) -> dict:
    return {"name": name, "status": status, "detail": detail, **extra}


def ops_token() -> str:
    token = os.environ.get("NOVA_OPS_TOKEN", "").strip()
    if token:
        return token
    try:
        for line in (REPO_ROOT / ".env").read_text(encoding="utf-8").splitlines():
            if line.startswith("NOVA_OPS_TOKEN="):
                return line.split("=", 1)[1].strip().strip("\"'")
    except OSError:
        pass
    return ""


def _opener():
    return urllib.request.build_opener(urllib.request.ProxyHandler({}))


def fetch_json(base: str, path: str, token: str, *, method: str = "GET", body: dict | None = None, timeout: float = 8.0) -> dict:
    data = json.dumps(body or {}).encode("utf-8") if method == "POST" else None
    headers = {"X-Nova-Ops-Token": token, "Content-Type": "application/json"}
    if method == "POST":
        # The CSRF middleware is a double-submit check; a service caller with
        # no browser session satisfies it with a self-consistent pair, the
        # same way nova-ops's BFF does (backend/docs/OPS_CONSOLE_API.md).
        csrf = secrets.token_hex(24)
        headers.update({"X-CSRF-Token": csrf, "Cookie": f"csrf_token={csrf}"})
    req = urllib.request.Request(f"{base}{path}", data=data, method=method, headers=headers)
    with _opener().open(req, timeout=timeout) as resp:  # noqa: S310 - fixed local URL
        return json.loads(resp.read().decode("utf-8"))


def mcp_probe(base: str, timeout: float = 5.0) -> int | None:
    """HTTP status of an unauthenticated POST to the MCP server (401 = up)."""
    req = urllib.request.Request(f"{base}/api/mcp/nova", data=b"{}", method="POST", headers={"Content-Type": "application/json", "Accept": "application/json, text/event-stream"})
    try:
        with _opener().open(req, timeout=timeout) as resp:  # noqa: S310
            return resp.status
    except urllib.error.HTTPError as exc:
        return exc.code
    except (urllib.error.URLError, OSError):
        return None


# ---------------------------------------------------------------- evaluation


def _load_gate_optional():
    """scripts/lib/gate_optional.py, or None when it cannot be imported."""
    try:
        sys.path.insert(0, str(REPO_ROOT / "scripts" / "lib"))
        import gate_optional  # type: ignore[import-not-found]
    except ImportError:
        return None
    return gate_optional


def evaluate_modules(ops: dict, optional_stacks: frozenset[str] | None = None) -> dict:
    """Configured-but-unhealthy modules are yellow.

    A module is only excused when every service it reports as "required
    failing" belongs to a stack declared in NOVA_GATE_OPTIONAL_STACKS -- a
    deployment that never ran Chatwoot/Twenty/OpenClaw should not hold the
    integrations module yellow forever. The excuse needs the gateway's full,
    untruncated list; anything else (another service failing, a detail we
    cannot parse) stays yellow.
    """
    status = ops.get("status", {})
    lib = _load_gate_optional() if optional_stacks else None
    bad, excused = [], {}
    for k, v in sorted(status.items()):
        if not v.get("configured") or v.get("healthy"):
            continue
        split = lib.failing_outside(str(v.get("detail", "")), optional_stacks) if lib else None
        if split and split[0] and not split[1]:
            excused[k] = split[0]
        else:
            bad.append(k)
    configured = sum(1 for v in status.values() if v.get("configured"))
    note = ""
    if excused:
        note = "; optional, not deployed (NOVA_GATE_OPTIONAL_STACKS): " + ", ".join(f"{k}: {', '.join(names)}" for k, names in excused.items())
    if bad:
        return check("modules", YELLOW, f"{len(bad)} configured module(s) unhealthy: " + ", ".join(f"{k} ({status[k].get('detail', '')})" for k in bad) + note, unhealthy=bad, optional=excused)
    return check("modules", GREEN, f"{configured}/{len(status)} module(s) configured, all healthy" + note, unhealthy=[], optional=excused)


def evaluate_contract(ops: dict, baseline: dict | None) -> dict:
    if baseline is None:
        return check("contract", YELLOW, f"{BASELINE.name} missing — run scripts/capabilities_snapshot.py")
    live = {op["name"]: op for op in ops.get("snapshot", {}).get("operations", [])}
    pinned = {op["name"]: op for op in baseline.get("operations", [])}
    missing = sorted(set(pinned) - set(live))
    changed = sorted(n for n in pinned if n in live and pinned[n] != live[n])
    added = sorted(set(live) - set(pinned))
    if missing or changed:
        return check("contract", RED, "live registry breaks the contract — missing: " + (", ".join(missing) or "none") + "; changed: " + (", ".join(changed) or "none"), missing=missing, changed=changed, added=added)
    if added:
        return check("contract", YELLOW, f"{len(added)} operation(s) not in the baseline; refresh it in the next commit: " + ", ".join(added), missing=[], changed=[], added=added)
    return check("contract", GREEN, f"{len(pinned)} operation(s) match the contract", missing=[], changed=[], added=[])


def evaluate_runtimes(rt: dict | None) -> dict:
    if rt is None:
        return check("runtimes", YELLOW, "runtimes.list unavailable")
    if not rt.get("enabled"):
        return check("runtimes", GREEN, "runtimes disabled (native only)", ready=["native"])
    ready = []
    for r in rt.get("runtimes", []):
        if r.get("kind") == "native" or (r.get("binary_on_path") and any(a.get("available") for a in r.get("accounts", []))):
            ready.append(r["id"])
    acp_ready = [r for r in ready if r != "native"]
    if not acp_ready:
        return check("runtimes", YELLOW, "runtimes enabled but no ACP runtime is ready (adapter on PATH + an available account)", ready=ready)
    return check("runtimes", GREEN, "ready: " + ", ".join(ready), ready=ready)


def evaluate_mcp(status: int | None) -> dict:
    if status == 401:
        return check("mcp_server", GREEN, "/api/mcp/nova up (bearer required)", http=401)
    if status == 503:
        return check("mcp_server", RED, "/api/mcp/nova answers 503 — session manager not started", http=503)
    if status is None:
        return check("mcp_server", RED, "/api/mcp/nova unreachable", http=None)
    return check("mcp_server", YELLOW, f"/api/mcp/nova answered HTTP {status} without a token (expected 401)", http=status)


def build_report(ops: dict | None, baseline: dict | None, runtimes: dict | None, *, mcp_status: int | None, error: str | None = None, optional_stacks: frozenset[str] | None = None) -> dict:
    if ops is None:
        checks = [check("registry", RED, f"capability registry unavailable: {error}")]
    else:
        checks = [evaluate_modules(ops, optional_stacks),evaluate_contract(ops, baseline), evaluate_runtimes(runtimes), evaluate_mcp(mcp_status)]
    overall = RED if any(c["status"] == RED for c in checks) else (YELLOW if any(c["status"] == YELLOW for c in checks) else GREEN)
    return {"gate": "capabilities", "checked_at_epoch": time.time(), "overall": overall, "ok": overall != RED, "checks": checks}


def write_atomic(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile("w", encoding="utf-8", dir=path.parent, prefix=".capabilities-gate-", suffix=".tmp", delete=False) as handle:
        json.dump(payload, handle, indent=2)
        handle.flush()
        os.fsync(handle.fileno())
        tmp = Path(handle.name)
    tmp.replace(path)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--base", default=os.environ.get("NOVA_GATEWAY_BASE", "http://127.0.0.1:2026"))
    parser.add_argument("--out", default=os.environ.get("NOVA_GATES_DIR", str(Path.home() / ".nova" / "gates")))
    parser.add_argument("--json", action="store_true")
    parser.add_argument("--print", dest="print_only", action="store_true", help="print only; do not write the status file")
    args = parser.parse_args(argv)

    token = ops_token()
    baseline = json.loads(BASELINE.read_text(encoding="utf-8")) if BASELINE.is_file() else None
    ops = runtimes = None
    error = None
    try:
        ops = fetch_json(args.base, "/api/capabilities/ops", token)
        try:
            runtimes = fetch_json(args.base, "/api/capabilities/ops/runtimes.list", token, method="POST").get("result")
        except (urllib.error.URLError, OSError, ValueError) as exc:
            runtimes = None
            error = f"runtimes.list: {exc}"
    except (urllib.error.URLError, OSError, ValueError) as exc:
        error = str(exc)
    lib = _load_gate_optional()
    optional = lib.optional_stacks() if lib else frozenset()
    report = build_report(ops, baseline, runtimes, mcp_status=mcp_probe(args.base), error=error, optional_stacks=optional)
    if not args.print_only:
        write_atomic(Path(args.out) / "capabilities.json", report)
    if args.json:
        print(json.dumps(report, indent=2))
    else:
        print(f"capabilities gate: {report['overall'].upper()} — " + "; ".join(f"{c['name']}={c['status']}" for c in report["checks"]))
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    sys.exit(main())
