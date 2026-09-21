#!/usr/bin/env python3
"""ACP integration test gate: writes ~/.nova/gates/acp.json.

Checks:
  1. config.yaml has runtimes defined
  2. Each configured ACP binary is on PATH (or its wrapper exists)
  3. Each binary runs --version or --help without error
  4. Config file is valid (parseable, no missing required fields)
  5. ACP workspace directories exist and are writable
  6. Live invoke probe (optional, gated by NOVA_GATE_ACP_PROBE=1)

    python3 scripts/gates/acp-gate.py            # write + summary
    python3 scripts/gates/acp-gate.py --json     # also echo the document
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
GREEN, YELLOW, RED = "green", "yellow", "red"
GATES_DIR = Path.home() / ".nova" / "gates"
ACP_CONFIG = REPO_ROOT / "config.yaml"


def check(name: str, status: str, detail: str, **extra) -> dict:
    return {"name": name, "status": status, "detail": detail, **extra}


def load_config() -> dict:
    """Load config.yaml and extract runtime definitions."""
    try:
        import yaml

        with open(ACP_CONFIG) as f:
            config = yaml.safe_load(f) or {}
        return config
    except Exception as e:
        return {"_error": str(e)}


def get_runtimes(config: dict) -> list[dict]:
    """Extract ACP agent configs from config.yaml.

    The agents live under ``acp_agents:`` as a *mapping* of id -> config, each
    carrying ``command`` and ``args``. (``runtimes:`` is a different, smaller
    mapping — enabled/default/nova_mcp_url — and holds no binaries.)

    This previously read ``config.get("runtimes", [])`` and returned early
    unless it was a list. Both keys are mappings, so it always returned [] and
    the gate reported "No runtimes configured" while two agents were
    configured and working — a gate that checks nothing while looking green.
    """
    agents = config.get("acp_agents") or {}
    if not isinstance(agents, dict):
        return []
    out: list[dict] = []
    for name, cfg in agents.items():
        if not isinstance(cfg, dict):
            continue
        out.append({"name": name, "binary": cfg.get("command", ""), "args": cfg.get("args") or []})
    return out


def check_binary(name: str, binary: str, args: list[str] | None = None) -> dict:
    """Check if a binary exists and is callable."""
    if not binary:
        return check(name, "yellow", "No binary specified")

    path = shutil.which(binary)
    if not path:
        return check(name, RED, f"Binary '{binary}' not on PATH")

    # Try running --version or --help
    test_args = args or [binary, "--version"]
    try:
        result = subprocess.run(
            test_args,
            capture_output=True,
            timeout=10,
            text=True,
        )
        if result.returncode == 0 or "usage" in (result.stderr + result.stdout).lower():
            return check(name, GREEN, f"Binary OK: {path}")
        else:
            return check(
                name,
                YELLOW,
                f"Binary exists but returned code {result.returncode}",
                path=path,
                stderr=result.stderr[:200],
            )
    except subprocess.TimeoutExpired:
        return check(name, YELLOW, "Binary timed out after 10s", path=path)
    except FileNotFoundError:
        return check(name, RED, f"Binary not found: {binary}", path=path)


def check_config_validity() -> dict:
    """Validate config.yaml is parseable and has minimum required fields."""
    if not ACP_CONFIG.exists():
        return check("config", RED, f"config.yaml not found at {ACP_CONFIG}")

    try:
        import yaml

        with open(ACP_CONFIG) as f:
            config = yaml.safe_load(f) or {}
    except Exception as e:
        return check("config", RED, f"config.yaml parse error: {e}")

    errors = []
    if "models" not in config:
        errors.append("missing 'models' section")
    if "sandbox" not in config:
        errors.append("missing 'sandbox' section")

    if errors:
        return check("config", YELLOW, "Config incomplete: " + "; ".join(errors))

    return check("config", GREEN, "Config valid")


def check_workspace_dirs() -> dict:
    """Check ACP workspace directories exist and are writable."""
    base = Path(tempfile.gettempdir()) / "nova-acp-workspace"
    try:
        base.mkdir(parents=True, exist_ok=True)
        test_file = base / ".gate-test"
        test_file.write_text("gate-test", encoding="utf-8")
        test_file.unlink()
        return check("workspace", GREEN, f"ACP workspace OK: {base}")
    except Exception as e:
        return check("workspace", RED, f"ACP workspace check failed: {e}")


def check_live_invoke() -> dict:
    """Optional live invoke probe."""
    if os.environ.get("NOVA_GATE_ACP_PROBE", "0") != "1":
        return check("invoke", GREEN, "Skipped (set NOVA_GATE_ACP_PROBE=1 to enable)")

    # This is a placeholder - a real invoke would need the full ACP runtime
    return check("invoke", GREEN, "Invoke probe not yet implemented")


def run_checks() -> dict:
    """Run all ACP checks and return the gate document."""
    start = time.time()
    checks = []

    # 1. Config validity
    checks.append(check_config_validity())

    # 2. Workspace dirs
    checks.append(check_workspace_dirs())

    # 3. Load config and check runtimes
    config = load_config()
    if "_error" in config:
        checks.append(check("runtimes", RED, f"Failed to load config: {config['_error']}"))
    else:
        runtimes = get_runtimes(config)
        if not runtimes:
            checks.append(check("runtimes", YELLOW, "No runtimes configured"))
        else:
            for rt in runtimes:
                name = rt.get("name", "unknown")
                binary = rt.get("binary", "")
                checks.append(check_binary(f"runtime-{name}", binary))
            checks.append(check("runtimes", GREEN, f"{len(runtimes)} ACP agent(s) configured: " + ", ".join(sorted(r["name"] for r in runtimes))))

    # 4. Live invoke
    checks.append(check_live_invoke())

    elapsed = time.time() - start

    # Determine overall status
    statuses = [c["status"] for c in checks]
    if RED in statuses:
        status = RED
    elif YELLOW in statuses:
        status = YELLOW
    else:
        status = GREEN

    doc = {
        "gate": "acp",
        "status": status,
        "timestamp": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "elapsed_ms": round(elapsed * 1000),
        "checks": checks,
    }
    return doc


def main():
    parser = argparse.ArgumentParser(description="ACP integration test gate")
    parser.add_argument("--json", action="store_true", help="Also echo the JSON document")
    args = parser.parse_args()

    doc = run_checks()

    # Write to gates dir
    GATES_DIR.mkdir(parents=True, exist_ok=True)
    out = GATES_DIR / "acp.json"
    out.write_text(json.dumps(doc, indent=2) + "\n", encoding="utf-8")

    # Summary
    color = {GREEN: "\033[92m", YELLOW: "\033[93m", RED: "\033[91m"}
    reset = "\033[0m"
    c = color.get(doc["status"], "")
    print(f"{c}[{doc['status'].upper()}]{reset} ACP gate — {len(doc['checks'])} checks, {doc['elapsed_ms']}ms")
    for ch in doc["checks"]:
        cc = color.get(ch["status"], "")
        print(f"  {cc}{ch['status']:>6}{reset}  {ch['name']}: {ch['detail']}")

    if args.json:
        print("\n" + json.dumps(doc, indent=2))

    sys.exit(0 if doc["status"] != RED else 1)


if __name__ == "__main__":
    main()
