#!/usr/bin/env python3
"""k3s-watchdog — host-level cron watchdog for the k3s cluster itself.

Unlike scripts/healthcheck-daemon.py (a PM2-supervised long-running asyncio
loop watching the *application* stack), this is a short-lived script meant
to be run periodically by cron — it checks whether k3s itself (the control
plane, not any specific workload inside it) is healthy, and takes the one
safe, reversible auto-fix action available: restarting the k3s systemd
unit if it's down or the node has gone NotReady.

Checks each run:
  - k3s.service is active (systemctl is-active k3s)
  - the node reports Ready (kubectl get nodes)
  - root filesystem usage (k3s's own data dir lives on this same disk on
    this box) — alert-only, no auto-fix; a full disk isn't something a
    service restart fixes
  - available memory — alert-only, same reasoning

Auto-fix: `sudo systemctl restart k3s` if the service isn't active OR the
node isn't Ready, but at most once per RESTART_COOLDOWN_S (default 10 min)
to avoid a restart-loop if the underlying cause isn't transient. Requires
the scoped sudoers rule set up alongside this script — see k8s/README.md.

Writes a single JSON status file (STATUS_PATH) that nova-ops's dashboard
reads directly (this runs on the same host nova-ops does — no gateway
hop needed, this isn't Nova application data). The file is structured as
a list of named checks + a bounded action history so more cron-based
watchdogs can extend it later without a nova-ops UI change.

Reads:
  - K3S_WATCHDOG_STATUS_PATH (default ~/.nova/k3s-watchdog-status.json)
  - K3S_WATCHDOG_KUBECONFIG (default ~/.kube/config)
  - K3S_WATCHDOG_DISK_PATH (default /)
  - K3S_WATCHDOG_DISK_WARN_PCT (default 85)
  - K3S_WATCHDOG_MEM_AVAILABLE_WARN_PCT (default 10 — warn if available memory drops below this % of total)
  - K3S_WATCHDOG_RESTART_COOLDOWN_S (default 600)
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

STATUS_PATH = Path(os.environ.get("K3S_WATCHDOG_STATUS_PATH", str(Path.home() / ".nova" / "k3s-watchdog-status.json")))
KUBECONFIG = os.environ.get("K3S_WATCHDOG_KUBECONFIG", str(Path.home() / ".kube" / "config"))
DISK_PATH = os.environ.get("K3S_WATCHDOG_DISK_PATH", "/")
DISK_WARN_PCT = float(os.environ.get("K3S_WATCHDOG_DISK_WARN_PCT", "85"))
MEM_AVAILABLE_WARN_PCT = float(os.environ.get("K3S_WATCHDOG_MEM_AVAILABLE_WARN_PCT", "10"))
RESTART_COOLDOWN_S = float(os.environ.get("K3S_WATCHDOG_RESTART_COOLDOWN_S", "600"))
MAX_ACTION_HISTORY = 20


def _run(cmd: list[str], timeout: float = 10.0) -> tuple[int, str, str]:
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
        return proc.returncode, proc.stdout.strip(), proc.stderr.strip()
    except (subprocess.TimeoutExpired, FileNotFoundError) as exc:
        return 1, "", str(exc)


def check_k3s_service() -> dict:
    code, out, err = _run(["systemctl", "is-active", "k3s"])
    active = code == 0 and out == "active"
    return {
        "name": "k3s_service",
        "ok": active,
        "detail": out or err or "unknown",
    }


def check_node_ready() -> dict:
    code, out, err = _run(
        ["kubectl", "--kubeconfig", KUBECONFIG, "get", "nodes", "-o", "json"],
        timeout=15.0,
    )
    if code != 0:
        return {"name": "node_ready", "ok": False, "detail": f"kubectl error: {err or out}"}
    try:
        data = json.loads(out)
    except json.JSONDecodeError:
        return {"name": "node_ready", "ok": False, "detail": "could not parse kubectl output"}

    items = data.get("items", [])
    if not items:
        return {"name": "node_ready", "ok": False, "detail": "no nodes returned"}

    not_ready = []
    for node in items:
        name = node.get("metadata", {}).get("name", "?")
        conditions = node.get("status", {}).get("conditions", [])
        ready_cond = next((c for c in conditions if c.get("type") == "Ready"), None)
        if not ready_cond or ready_cond.get("status") != "True":
            not_ready.append(name)

    if not_ready:
        return {"name": "node_ready", "ok": False, "detail": f"not Ready: {', '.join(not_ready)}"}
    return {"name": "node_ready", "ok": True, "detail": f"{len(items)} node(s) Ready"}


def check_disk() -> dict:
    usage = shutil.disk_usage(DISK_PATH)
    pct_used = (usage.used / usage.total) * 100
    ok = pct_used < DISK_WARN_PCT
    return {
        "name": "disk",
        "ok": ok,
        "detail": f"{pct_used:.1f}% used on {DISK_PATH} ({usage.free // (1024**3)}GiB free)",
    }


def check_memory() -> dict:
    meminfo: dict[str, int] = {}
    try:
        with open("/proc/meminfo", encoding="utf-8") as fh:
            for line in fh:
                parts = line.split(":")
                if len(parts) != 2:
                    continue
                key = parts[0].strip()
                value = parts[1].strip().split()[0]
                meminfo[key] = int(value)
    except OSError as exc:
        return {"name": "memory", "ok": False, "detail": f"could not read /proc/meminfo: {exc}"}

    total = meminfo.get("MemTotal", 0)
    available = meminfo.get("MemAvailable", 0)
    if total == 0:
        return {"name": "memory", "ok": False, "detail": "MemTotal unavailable"}
    pct_available = (available / total) * 100
    ok = pct_available >= MEM_AVAILABLE_WARN_PCT
    return {
        "name": "memory",
        "ok": ok,
        "detail": f"{pct_available:.1f}% available ({available // 1024}MiB of {total // 1024}MiB)",
    }


def maybe_restart_k3s(status: dict, checks: list[dict]) -> dict | None:
    """Attempt `sudo systemctl restart k3s` if k3s or the node is unhealthy,
    respecting the cooldown so a persistent (non-transient) failure doesn't
    trigger a restart loop."""
    k3s_check = next((c for c in checks if c["name"] == "k3s_service"), None)
    node_check = next((c for c in checks if c["name"] == "node_ready"), None)
    needs_restart = (k3s_check and not k3s_check["ok"]) or (node_check and not node_check["ok"])
    if not needs_restart:
        return None

    history = status.get("action_history", [])
    last_restart = next((a for a in reversed(history) if a["action"] == "restart_k3s"), None)
    now = time.time()
    if last_restart and (now - last_restart["at_epoch"]) < RESTART_COOLDOWN_S:
        return {
            "action": "restart_k3s_skipped_cooldown",
            "at_epoch": now,
            "detail": f"last restart attempt was {now - last_restart['at_epoch']:.0f}s ago, cooldown is {RESTART_COOLDOWN_S:.0f}s",
        }

    code, out, err = _run(["sudo", "-n", "systemctl", "restart", "k3s"], timeout=30.0)
    return {
        "action": "restart_k3s",
        "at_epoch": now,
        "detail": "restart succeeded" if code == 0 else f"restart failed: {err or out}",
        "success": code == 0,
    }


def main() -> int:
    STATUS_PATH.parent.mkdir(parents=True, exist_ok=True)

    previous_status: dict = {}
    if STATUS_PATH.exists():
        try:
            previous_status = json.loads(STATUS_PATH.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            previous_status = {}

    checks = [check_k3s_service(), check_node_ready(), check_disk(), check_memory()]
    all_ok = all(c["ok"] for c in checks)

    action = maybe_restart_k3s(previous_status, checks)
    history = list(previous_status.get("action_history", []))
    if action:
        history.append(action)
        history = history[-MAX_ACTION_HISTORY:]

    status = {
        "checked_at_epoch": time.time(),
        "ok": all_ok,
        "checks": checks,
        "action_history": history,
    }

    tmp_path = STATUS_PATH.with_suffix(".tmp")
    tmp_path.write_text(json.dumps(status, indent=2), encoding="utf-8")
    tmp_path.replace(STATUS_PATH)

    if not all_ok:
        print(f"k3s-watchdog: UNHEALTHY — {[c for c in checks if not c['ok']]}", file=sys.stderr)
        return 1
    print("k3s-watchdog: OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
