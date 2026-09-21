#!/usr/bin/env python3
"""Sandbox health gate: ensures the Agent's Computer image chain is intact.

Writes ~/.nova/gates/sandbox-health.json. This is the gate that catches the
class of failure where the sandbox image disappears and nothing notices.

Checks (red/yellow/green):

- ``image_chain``     all 4 Docker image layers exist (base, tools, dind, android)
- ``image_manifest``  the configured image boots and exposes /etc/nova-sandbox.json
- ``tool_inventory``  every binary verify-toolchain.sh asserts is present — read
                      from the image manifest, not a hardcoded subset
- ``vendor_integrity`` the staged vendor/ directory has the binaries needed to
                       rebuild the image without network downloads
- ``host_resources``  disk, memory, swap, IO pressure are within bounds
- ``auto_rebuild``    if the image is missing but vendor is intact, trigger rebuild

    python3 scripts/gates/sandbox-health-gate.py            # write + summary
    python3 scripts/gates/sandbox-health-gate.py --json     # echo the document
    python3 scripts/gates/sandbox-health-gate.py --rebuild  # force rebuild if image missing
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import tempfile
import time
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
SANDBOX_DIR = REPO_ROOT / "docker" / "sandbox"
GREEN, YELLOW, RED = "green", "yellow", "red"

# The 4-layer image chain. Each must exist for the Agent's Computer to start.
IMAGE_CHAIN = [
    ("base", "nova-sandbox-base:latest"),
    ("tools", "nova-sandbox-tools:latest"),
    ("dind", "nova-sandbox-dind:latest"),
    ("android", "nova-sandbox-android:latest"),
]

# Host resource thresholds — red if exceeded.
DISK_WARN_PERCENT = 85
DISK_RED_PERCENT = 95
MEMORY_WARN_PERCENT = 80
SWAP_WARN_PERCENT = 60
IO_PRESSURE_WARN = 20.0  # avg60 percentage


def check(name: str, status: str, detail: str, **extra) -> dict:
    return {"name": name, "status": status, "detail": detail, **extra}


def run(cmd: list[str], timeout: float = 60) -> tuple[int, str]:
    try:
        p = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout, check=False)
        return p.returncode, (p.stdout or "") + (p.stderr or "")
    except (OSError, subprocess.TimeoutExpired) as exc:
        return 127, str(exc)


def read_env(key: str) -> str | None:
    value = os.environ.get(key)
    if value is not None:
        return value
    try:
        for line in (REPO_ROOT / ".env").read_text(encoding="utf-8").splitlines():
            if line.startswith(key + "="):
                return line.split("=", 1)[1].strip().strip("\"'")
    except OSError:
        pass
    return None


def config_text() -> str:
    try:
        return (REPO_ROOT / "config.yaml").read_text(encoding="utf-8")
    except OSError:
        return ""


def config_value(text: str, section: str, key: str) -> str | None:
    import re
    m = re.search(rf"^{section}:\s*$(?:\n[ \t]+.*)*?\n[ \t]+{key}:\s*([^\s#]+)", text, re.MULTILINE)
    return m.group(1) if m else None


# ---------------------------------------------------------------- image chain


def evaluate_image_chain() -> dict:
    """Check all 4 layers of the sandbox image chain exist."""
    results = []
    for layer, tag in IMAGE_CHAIN:
        rc, _ = run(["docker", "image", "inspect", tag], timeout=30)
        results.append({"layer": layer, "tag": tag, "exists": rc == 0})

    missing = [r for r in results if not r["exists"]]
    if not missing:
        return check("image_chain", GREEN,
                      f"all {len(IMAGE_CHAIN)} layers present: " +
                      ", ".join(r["tag"] for r in results),
                      chain={r["layer"]: r["tag"] for r in results})
    if len(missing) < len(IMAGE_CHAIN):
        return check("image_chain", YELLOW,
                      f"{len(IMAGE_CHAIN) - len(missing)}/{len(IMAGE_CHAIN)} layers present; missing: " +
                      ", ".join(r["tag"] for r in missing),
                      missing=[r["tag"] for r in missing])
    return check("image_chain", RED,
                  f"all {len(IMAGE_CHAIN)} layers missing — Agent's Computer cannot start",
                  missing=[r["tag"] for r in missing])


# ---------------------------------------------------------------- image manifest


def configured_image() -> str | None:
    text = config_text()
    return config_value(text, "sandbox", "image") if text else None


def image_exists(image: str) -> bool:
    return run(["docker", "image", "inspect", image], timeout=30)[0] == 0


def sandbox_manifest(image: str) -> dict | None:
    rc, out = run(["docker", "run", "--rm", "--entrypoint", "cat", image, "/etc/nova-sandbox.json"], timeout=120)
    if rc != 0:
        return None
    try:
        return json.loads(out)
    except ValueError:
        return None


def evaluate_image_manifest(image: str | None, exists: bool, manifest: dict | None) -> dict:
    if not image:
        return check("image_manifest", RED, "config.yaml has no sandbox.image")
    if not exists:
        return check("image_manifest", RED,
                      f"{image} not on Docker daemon — run: make sandbox-image",
                      image=image)
    if manifest is None:
        return check("image_manifest", YELLOW,
                      f"{image} exists but /etc/nova-sandbox.json unreadable (pre-manifest build?)",
                      image=image)
    return check("image_manifest", GREEN,
                  f"{image} layer={manifest.get('layer')} build={manifest.get('build_id')}",
                  image=image, layer=manifest.get("layer"),
                  build_id=manifest.get("build_id"))


# ---------------------------------------------------------------- tool inventory


def tool_inventory_from_manifest(manifest: dict) -> dict[str, str]:
    """Read the full tool inventory from the image's /etc/nova-sandbox.json."""
    return manifest.get("toolchain", {})


def tool_inventory_live(image: str) -> dict[str, str] | None:
    """Live inventory from inside the image, or None when the probe could not run.

    The distinction matters more than it looks. This returned ``{}`` on any
    failure -- non-zero exit, timeout, unparseable output -- and the evaluator
    below reads an empty inventory as "every tool in the manifest is missing".
    On 2026-09-21 the probe container could not start under IO pressure and
    the gate reported all 78 tools gone from a perfectly intact image. Every
    one of them was verified present by hand minutes later.

    A gate that reports catastrophe when it merely could not look is worse
    than one that stays quiet: it is the fastest way to teach everyone to
    ignore the board.
    """
    rc, out = run(["docker", "run", "--rm", "--entrypoint", "sh", image,
                    "-c", "HOME=/tmp /usr/local/bin/verify-toolchain.sh --manifest"],
                   timeout=180)
    if rc != 0:
        return None
    try:
        parsed = json.loads(out.strip())
    except ValueError:
        return None
    return parsed if isinstance(parsed, dict) else None


def evaluate_tool_inventory(image_ok: bool, manifest_tools: dict, live_tools: dict | None) -> dict:
    if not image_ok:
        return check("tool_inventory", RED, "no sandbox image to inspect")

    if live_tools is None:
        # Could not look. Say so, rather than reporting the image as gutted.
        return check("tool_inventory", YELLOW,
                      "could not probe the image (container would not start or returned no manifest) — "
                      "tool inventory unverified, not known-bad")

    # Compare manifest vs live — a mismatch means the image was modified after build
    manifest_names = set(manifest_tools.keys())
    live_names = set(live_tools.keys())

    missing_in_live = manifest_names - live_names
    extra_in_live = live_names - manifest_names

    if not manifest_names:
        return check("tool_inventory", YELLOW,
                      "manifest has no toolchain entries (pre-manifest image?)")

    if missing_in_live:
        return check("tool_inventory", RED,
                      f"{len(missing_in_live)} tools in manifest but missing at runtime: " +
                      ", ".join(sorted(missing_in_live)[:20]),
                      missing=sorted(missing_in_live))

    total = len(manifest_names)
    if extra_in_live:
        return check("tool_inventory", GREEN,
                      f"{total} tools present ({len(extra_in_live)} extra at runtime)",
                      total=total, extra=sorted(extra_in_live))

    return check("tool_inventory", GREEN,
                  f"{total} tools present, manifest matches runtime",
                  total=total)


# ---------------------------------------------------------------- vendor integrity


def evaluate_vendor_integrity() -> dict:
    """Check that vendor/ has the binaries needed to rebuild without network."""
    if not SANDBOX_DIR.is_dir():
        return check("vendor_integrity", RED, "docker/sandbox/ directory missing")

    vendor = SANDBOX_DIR / "vendor"
    if not vendor.is_dir():
        return check("vendor_integrity", RED, "docker/sandbox/vendor/ missing — rebuild will download everything")

    # Critical vendored binaries (expensive to download on throttled ISP)
    critical = ["go.tar", "rust.tar", "uv", "kubectl", "helm", "terraform",
                "nuclei", "httpx", "subfinder", "gitleaks", "trufflehog",
                "dalfox", "trivy"]
    present = [f for f in critical if (vendor / f).exists() or (vendor / f"{f}.tar").exists()]
    missing = [f for f in critical if f not in present]

    # Android SDK
    android_ok = (vendor / "android" / "gradle").is_dir() and (vendor / "android" / "android-sdk").is_dir()

    # Wheels
    wheels = vendor / "wheels"
    wheel_count = len(list(wheels.glob("*.whl"))) if wheels.is_dir() else 0

    total_size = sum(f.stat().st_size for f in vendor.rglob("*") if f.is_file()) / (1024**3)

    if missing and not android_ok:
        return check("vendor_integrity", RED,
                      f"{len(missing)} critical binaries missing, Android SDK missing — "
                      f"rebuild requires full network download ({total_size:.1f}GB staged)",
                      missing=missing, android=android_ok, wheels=wheel_count)

    if missing:
        return check("vendor_integrity", YELLOW,
                      f"{len(missing)} critical binaries missing (will download): " +
                      ", ".join(missing),
                      missing=missing, android=android_ok, wheels=wheel_count,
                      staged_gb=round(total_size, 1))

    return check("vendor_integrity", GREEN,
                  f"{len(present)} critical binaries staged, Android SDK {'present' if android_ok else 'missing'}, "
                  f"{wheel_count} wheels, {total_size:.1f}GB total",
                  android=android_ok, wheels=wheel_count, staged_gb=round(total_size, 1))


# ---------------------------------------------------------------- host resources


def evaluate_host_resources() -> dict:
    """Check disk, memory, swap, IO pressure."""
    problems = []
    metrics = {}

    # Disk
    try:
        st = os.statvfs("/")
        total = st.f_blocks * st.f_frsize
        free = st.f_bavail * st.f_frsize
        used_pct = (1 - free / total) * 100
        free_gb = free / (1024**3)
        metrics["disk_percent"] = round(used_pct, 1)
        metrics["disk_free_gb"] = round(free_gb, 1)
        if used_pct >= DISK_RED_PERCENT:
            problems.append(f"disk {used_pct:.0f}% used ({free_gb:.0f}GB free) — RED")
        elif used_pct >= DISK_WARN_PERCENT:
            problems.append(f"disk {used_pct:.0f}% used ({free_gb:.0f}GB free) — YELLOW")
    except OSError:
        pass

    # Memory
    try:
        with open("/proc/meminfo") as f:
            meminfo = {}
            for line in f:
                parts = line.split()
                meminfo[parts[0].rstrip(":")] = int(parts[1]) * 1024  # kB -> bytes
        total_mem = meminfo.get("MemTotal", 1)
        avail_mem = meminfo.get("MemAvailable", 0)
        used_pct = (1 - avail_mem / total_mem) * 100
        metrics["memory_percent"] = round(used_pct, 1)
        metrics["memory_avail_gb"] = round(avail_mem / (1024**3), 1)
        if used_pct >= MEMORY_WARN_PERCENT:
            problems.append(f"memory {used_pct:.0f}% used ({metrics['memory_avail_gb']}GB available)")
    except (OSError, ValueError):
        pass

    # Swap
    try:
        with open("/proc/meminfo") as f:
            meminfo = {}
            for line in f:
                parts = line.split()
                meminfo[parts[0].rstrip(":")] = int(parts[1]) * 1024
        total_swap = meminfo.get("SwapTotal", 1)
        free_swap = meminfo.get("SwapFree", 0)
        used_pct = (1 - free_swap / total_swap) * 100 if total_swap > 0 else 0
        metrics["swap_percent"] = round(used_pct, 1)
        if used_pct >= SWAP_WARN_PERCENT:
            problems.append(f"swap {used_pct:.0f}% used")
    except (OSError, ValueError):
        pass

    # IO pressure — /proc/pressure/io has "some" and "full" lines, each
    # with avg10/avg60/avg300/total. We read the "full" line (all tasks
    # stalled) and extract avg60. The old code checked
    # line.startswith("avg60=") which never matches the file format.
    try:
        with open("/proc/pressure/io") as f:
            for line in f:
                if line.startswith("full"):
                    for token in line.split():
                        if token.startswith("avg60="):
                            avg60 = float(token.split("=")[1])
                            metrics["io_pressure_avg60"] = avg60
                            if avg60 >= IO_PRESSURE_WARN:
                                problems.append(f"IO pressure avg60={avg60:.1f}%")
    except (OSError, ValueError):
        pass

    if problems:
        status = RED if any("RED" in p for p in problems) else YELLOW
        return check("host_resources", status, "; ".join(problems), **metrics)
    return check("host_resources", GREEN,
                  f"disk {metrics.get('disk_percent', '?')}%, "
                  f"memory {metrics.get('memory_percent', '?')}%, "
                  f"swap {metrics.get('swap_percent', '?')}%, "
                  f"IO {metrics.get('io_pressure_avg60', '?')}%",
                  **metrics)


# ---------------------------------------------------------------- auto rebuild


def evaluate_auto_rebuild(image_ok: bool, vendor_ok: bool, rebuild: bool) -> dict:
    """If image is missing but vendor is intact, trigger rebuild."""
    if image_ok:
        return check("auto_rebuild", GREEN, "image present — no rebuild needed")

    if not vendor_ok:
        return check("auto_rebuild", YELLOW,
                      "image missing and vendor/ incomplete — manual rebuild required")

    if not rebuild:
        return check("auto_rebuild", YELLOW,
                      "image missing but vendor/ intact — run with --rebuild to auto-rebuild, "
                      "or: cd docker/sandbox && ./build.sh android")

    # Trigger the build
    build_script = SANDBOX_DIR / "build.sh"
    if not build_script.exists():
        return check("auto_rebuild", RED, f"build script not found: {build_script}")

    log_path = Path("/tmp/nova-sandbox-rebuild.log")
    rc = subprocess.Popen(
        ["bash", str(build_script), "android"],
        stdout=open(log_path, "w"),
        stderr=subprocess.STDOUT,
        cwd=str(SANDBOX_DIR),
    )

    return check("auto_rebuild", YELLOW,
                  f"rebuild triggered (PID {rc.pid}), log: {log_path}",
                  rebuild_pid=rc.pid, log=str(log_path))


# ---------------------------------------------------------------- report


def build_report(checks: list[dict]) -> dict:
    overall = RED if any(c["status"] == RED for c in checks) else (
        YELLOW if any(c["status"] == YELLOW for c in checks) else GREEN
    )
    return {
        "gate": "sandbox-health",
        "checked_at_epoch": time.time(),
        "overall": overall,
        "ok": overall != RED,
        "checks": checks,
    }


def write_atomic(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile("w", encoding="utf-8", dir=path.parent,
                                     prefix=".sandbox-health-", suffix=".tmp",
                                     delete=False) as handle:
        json.dump(payload, handle, indent=2)
        handle.flush()
        os.fsync(handle.fileno())
        tmp = Path(handle.name)
    tmp.replace(path)


# ---------------------------------------------------------------- main


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--out", default=os.environ.get("NOVA_GATES_DIR",
                        str(Path.home() / ".nova" / "gates")))
    parser.add_argument("--json", action="store_true")
    parser.add_argument("--rebuild", action="store_true",
                        help="trigger rebuild if image is missing")
    args = parser.parse_args(argv)

    checks = []

    # 1. Image chain — all 4 layers must exist
    checks.append(evaluate_image_chain())

    # 2. Configured image manifest
    image = configured_image()
    exists = bool(image) and image_exists(image)
    manifest = sandbox_manifest(image) if exists else None
    checks.append(evaluate_image_manifest(image, exists, manifest))

    # 3. Tool inventory — from manifest + live verification
    manifest_tools = tool_inventory_from_manifest(manifest) if manifest else {}
    live_tools = tool_inventory_live(image) if exists else {}
    checks.append(evaluate_tool_inventory(exists, manifest_tools, live_tools))

    # 4. Vendor integrity — can we rebuild without network?
    vendor_check = evaluate_vendor_integrity()
    checks.append(vendor_check)
    vendor_ok = vendor_check["status"] != RED

    # 5. Host resources
    checks.append(evaluate_host_resources())

    # 6. Auto-rebuild
    checks.append(evaluate_auto_rebuild(exists, vendor_ok, args.rebuild))

    report = build_report(checks)
    write_atomic(Path(args.out) / "sandbox-health.json", report)

    if args.json:
        print(json.dumps(report, indent=2))
    else:
        print(f"sandbox-health gate: {report['overall'].upper()}")
        for c in checks:
            print(f"  [{c['status'].upper():6}] {c['name']:18} {c['detail'][:160]}")
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    sys.exit(main())
