#!/usr/bin/env python3
"""Deployment drift gate for the Nova Ops console.

Why this exists
---------------
"I recreated the container and the project started breaking on its own" is not
diagnosable after the fact, because nothing records what the running system was
built from. This gate makes the answer visible continuously: is what is running
actually what the repository says should be running?

Each check corresponds to a way Nova has drifted in practice.

A note on the compose check
---------------------------
Docker Compose's `com.docker.compose.project.config_files` label records, per
service, only the files that *contribute to that service* -- not the whole -f
chain. The gateway legitimately shows `dev,dood,voice` while the frontend shows
`dev,dood,prod-frontend`, from one single `docker compose up`, because
prod-frontend.yaml defines only `frontend` and voice.yaml modifies only
`gateway`. Comparing either service's label against the full canonical chain
therefore reports drift that does not exist. This gate compares the *union*
across all Nova services instead, which is the thing that must match.

Usage:
    scripts/gates/drift-gate.py           # write status file, print summary
    scripts/gates/drift-gate.py --json
    scripts/gates/drift-gate.py --print   # do not write the status file
"""

from __future__ import annotations

import argparse
import datetime as _dt
import json
import os
import re
import subprocess
import sys
import tempfile
import time
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
GREEN, YELLOW, RED = "green", "yellow", "red"
NOVA_SERVICES = ("deer-flow-gateway", "deer-flow-frontend", "deer-flow-nginx")


def _status_path() -> Path:
    override = os.environ.get("NOVA_DRIFT_GATE_STATUS_PATH", "").strip()
    return (
        Path(override) if override else Path.home() / ".nova" / "gates" / "drift.json"
    )


def check(name: str, status: str, detail: str, **extra) -> dict:
    return {"name": name, "status": status, "detail": detail, **extra}


def _run(cmd: list[str], timeout: float = 60) -> tuple[int, str, str]:
    try:
        p = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
        return p.returncode, p.stdout.strip(), p.stderr.strip()
    except subprocess.TimeoutExpired:
        return 124, "", f"timed out after {timeout:.0f}s"
    except (OSError, subprocess.SubprocessError) as exc:
        return 1, "", str(exc)


def check_git_clean() -> dict:
    rc, out, _ = _run(["git", "-C", str(REPO_ROOT), "status", "--porcelain"])
    if rc != 0:
        return check("git_worktree", YELLOW, "not a git repository")
    changed = [line for line in out.splitlines() if line.strip()]
    if not changed:
        return check("git_worktree", GREEN, "clean")
    return check(
        "git_worktree",
        YELLOW,
        f"{len(changed)} uncommitted change(s) — running code is not "
        "reproducible from any commit",
        files=[c[3:] for c in changed[:20]],
    )


def check_config_version() -> dict:
    """config.yaml is gitignored, so nothing else can catch it falling behind."""

    def version_of(path: Path) -> int | None:
        try:
            for line in path.read_text().splitlines():
                m = re.match(r"^config_version:\s*(\d+)", line)
                if m:
                    return int(m.group(1))
        except OSError:
            return None
        return None

    live = version_of(REPO_ROOT / "config.yaml")
    example = version_of(REPO_ROOT / "config.example.yaml")
    if live is None or example is None:
        return check("config_version", YELLOW, "could not read config_version")
    if live == example:
        return check("config_version", GREEN, f"v{live}, matches example")
    status = RED if example - live >= 3 else YELLOW
    return check(
        "config_version",
        status,
        f"config.yaml is v{live}, config.example.yaml is v{example} "
        "— run `make config-upgrade`",
        live=live,
        example=example,
    )


# Hash every file under frontend/src identically on both sides. LC_ALL=C is
# load-bearing: the host sorts with a locale-aware collation and the container
# with C, so `./app/[lang]/...` lands in a different position and the digests
# differ for two byte-identical trees.
_SRC_HASH_CMD = (
    "export LC_ALL=C; cd {path} && find . -type f -print0 | sort -z "
    "| xargs -0 sha256sum 2>/dev/null | sha256sum | cut -d' ' -f1"
)


def check_frontend_build_freshness() -> dict:
    """Is the running frontend actually serving the current source?

    docker-compose.prod-frontend.yaml runs `next start`, which serves the
    `.next` build baked into the image, while the dev stack's `volumes:` still
    bind-mounts frontend/src over it (compose merges volumes by appending, so
    the override cannot remove the mount). The container therefore *looks* like
    it is running your source while serving a build from whenever the image was
    last made — every edit silently invisible. That trap cost a full debugging
    cycle this session.

    Compare content, not timestamps. Earlier revisions of this check used file
    mtimes and then last-commit time; both fire spuriously after a `git
    checkout` or a merge, which rewrite the working tree without changing a
    byte. Two false positives in a row is how a gate gets ignored.

    The image bakes its own copy of frontend/src, so hashing that (via `docker
    run`, which bypasses the bind mount that `docker exec` would see) against
    the working tree answers the question exactly, and needs no build-pipeline
    change or recorded marker.
    """
    rc, image, _ = _run(
        ["docker", "inspect", "deer-flow-frontend", "--format", "{{.Config.Image}}"],
        timeout=30,
    )
    if rc != 0 or not image:
        return check("frontend_build", YELLOW, "frontend container not running")

    src = REPO_ROOT / "frontend" / "src"
    if not src.is_dir():
        return check("frontend_build", YELLOW, "frontend/src not found")

    # A dev server compiles from the bind mount on every request, so the image's
    # baked copy is not what is being served and hashing it answers the wrong
    # question. Without this the check reports RED forever the moment the stack
    # runs `next dev` -- and a stale `.next/BUILD_ID` sits in the container to
    # make the message look convincing. Per the note above: two false positives
    # in a row is how a gate gets ignored.
    #
    # Dev mode is only trustworthy if the mount is actually there, so both are
    # required before this short-circuits.
    rc_c, cmd, _ = _run(
        ["docker", "inspect", "deer-flow-frontend", "--format", "{{json .Config.Cmd}}"],
        timeout=30,
    )
    is_dev_server = rc_c == 0 and ("next dev" in cmd or "run dev" in cmd)

    rc_m, mounts, _ = _run(
        [
            "docker",
            "inspect",
            "deer-flow-frontend",
            "--format",
            "{{range .Mounts}}{{.Source}}=>{{.Destination}} {{end}}",
        ],
        timeout=30,
    )
    src_is_mounted = rc_m == 0 and f"{src}=>/app/frontend/src" in mounts

    if is_dev_server:
        if src_is_mounted:
            return check(
                "frontend_build",
                GREEN,
                "dev server compiling frontend/src live from the bind mount",
            )
        return check(
            "frontend_build",
            RED,
            "running a dev server but frontend/src is NOT bind-mounted — it is "
            "compiling the image's baked copy, so your edits are invisible",
        )

    rc_h, host_hash, _ = _run(
        ["bash", "-c", _SRC_HASH_CMD.format(path=str(src))], timeout=120
    )
    if rc_h != 0 or not host_hash:
        return check("frontend_build", YELLOW, "could not hash frontend/src")

    rc_i, image_hash, err = _run(
        [
            "docker",
            "run",
            "--rm",
            "--entrypoint",
            "sh",
            "-e",
            "LC_ALL=C",
            image,
            "-c",
            _SRC_HASH_CMD.format(path="/app/frontend/src"),
        ],
        timeout=180,
    )
    if rc_i != 0 or not image_hash:
        return check(
            "frontend_build",
            YELLOW,
            f"could not hash the image's source: {err[:100] or 'unknown'}",
        )

    rc_b, build_id, _ = _run(
        ["docker", "exec", "deer-flow-frontend", "cat", "/app/frontend/.next/BUILD_ID"],
        timeout=30,
    )
    build = build_id.strip() or "unknown"

    if host_hash.strip() == image_hash.strip():
        return check(
            "frontend_build",
            GREEN,
            f"served build {build} matches frontend/src exactly",
            build_id=build,
        )
    return check(
        "frontend_build",
        RED,
        f"frontend/src differs from the source baked into the served "
        f"build ({build}) — those changes are NOT live; rebuild the image",
        build_id=build,
        host_hash=host_hash.strip()[:16],
        image_hash=image_hash.strip()[:16],
    )


def check_compose_chain() -> dict:
    """Union of per-service config_files vs the canonical chain."""
    canonical = {
        "docker-compose-dev.yaml",
        "docker-compose.dood.yaml",
        "docker-compose.prod-frontend.yaml",
    }
    speech_on = False
    try:
        text = (REPO_ROOT / "config.yaml").read_text()
        m = re.search(r"^speech:\s*$\s+enabled:\s*(\w+)", text, re.MULTILINE)
        speech_on = bool(m and m.group(1).lower() == "true")
    except OSError:
        pass
    if speech_on:
        canonical.add("docker-compose.voice.yaml")

    seen: set[str] = set()
    missing_containers = []
    for name in NOVA_SERVICES:
        rc, out, _ = _run(
            [
                "docker",
                "inspect",
                name,
                "--format",
                '{{index .Config.Labels "com.docker.compose.project.config_files"}}',
            ],
            timeout=30,
        )
        if rc != 0 or not out:
            missing_containers.append(name)
            continue
        seen.update(Path(p).name for p in out.split(","))

    if missing_containers and len(missing_containers) == len(NOVA_SERVICES):
        return check("compose_chain", YELLOW, "no Nova containers running")

    if seen == canonical:
        return check(
            "compose_chain", GREEN, f"{len(seen)} overlay(s) match the canonical chain"
        )
    extra, absent = seen - canonical, canonical - seen
    bits = []
    if absent:
        bits.append(f"missing {sorted(absent)}")
    if extra:
        bits.append(f"unexpected {sorted(extra)}")
    return check(
        "compose_chain",
        YELLOW,
        "; ".join(bits) + " — containers were not brought up by "
        "scripts/pm2-deerflow.sh",
        seen=sorted(seen),
        canonical=sorted(canonical),
    )


def check_pm2_apps() -> dict:
    """PM2's running set vs what ecosystem.config.js declares."""
    eco = REPO_ROOT / "ecosystem.config.js"
    try:
        declared = set(re.findall(r'name:\s*["\']([^"\']+)["\']', eco.read_text()))
    except OSError:
        return check("pm2_apps", YELLOW, "ecosystem.config.js unreadable")

    rc, out, _ = _run(["pm2", "jlist"], timeout=45)
    if rc != 0:
        return check("pm2_apps", YELLOW, "pm2 not reachable")
    try:
        running = {
            a["name"]: a["pm2_env"].get("status")
            for a in json.loads(out)
            if a.get("name") in declared
        }
    except (json.JSONDecodeError, KeyError, TypeError):
        return check("pm2_apps", YELLOW, "could not parse pm2 output")

    absent = sorted(declared - set(running))
    stopped = sorted(n for n, st in running.items() if st != "online")
    if not absent and not stopped:
        return check("pm2_apps", GREEN, f"all {len(declared)} declared apps online")
    bits = []
    if absent:
        bits.append(f"not registered: {absent}")
    if stopped:
        bits.append(f"not online: {stopped}")
    return check(
        "pm2_apps",
        RED if absent else YELLOW,
        "; ".join(bits),
        declared=sorted(declared),
    )


def check_restart_storm() -> dict:
    """Catch autoheal masking a crash instead of fixing one.

    docker-compose-dev.yaml runs a `willfarrell/autoheal` sidecar that restarts
    the gateway when its healthcheck fails. That converts a hung gateway into a
    ~15s blip, which is the point -- but it also means a gateway crashing every
    few minutes looks healthy from outside while quietly losing every in-flight
    run. A restart is a symptom; a stream of them is an outage wearing a
    disguise.

    RestartCount is cumulative for the life of the container, so it is read
    against container uptime rather than as an absolute: three restarts over a
    week is noise, three in an hour is not.
    """
    findings = []
    worst = GREEN

    for name in NOVA_SERVICES:
        rc, out, _ = _run(
            [
                "docker",
                "inspect",
                name,
                "--format",
                "{{.RestartCount}}|{{.State.StartedAt}}|{{.State.OOMKilled}}",
            ],
            timeout=30,
        )
        if rc != 0 or not out:
            continue
        parts = out.split("|")
        if len(parts) < 3:
            continue
        try:
            restarts = int(parts[0])
        except ValueError:
            continue
        oom = parts[2].strip().lower() == "true"

        started = parts[1].strip()
        hours = None
        try:
            # Docker emits RFC3339 with nanoseconds, which %f cannot parse.
            cleaned = re.sub(r"\.(\d{6})\d*", r".\1", started).replace("Z", "+00:00")
            hours = (
                _dt.datetime.now(_dt.timezone.utc) - _dt.datetime.fromisoformat(cleaned)
            ).total_seconds() / 3600
        except (ValueError, TypeError):
            pass

        if oom:
            findings.append(f"{name}: OOM-killed")
            worst = RED
            continue
        if restarts == 0:
            continue

        if hours is not None and hours > 0:
            rate = restarts / hours
            if rate >= 2:
                findings.append(f"{name}: {restarts} restarts in {hours:.1f}h")
                worst = RED
            elif restarts >= 3:
                findings.append(f"{name}: {restarts} restarts over {hours:.1f}h")
                worst = YELLOW if worst == GREEN else worst
        elif restarts >= 3:
            findings.append(f"{name}: {restarts} restarts")
            worst = YELLOW if worst == GREEN else worst

    if not findings:
        return check("restart_storm", GREEN, "no repeated restarts")
    return check(
        "restart_storm",
        worst,
        "; ".join(findings) + " — autoheal may be masking a crash loop",
    )


CHECKS = (
    check_git_clean,
    check_config_version,
    check_frontend_build_freshness,
    check_compose_chain,
    check_pm2_apps,
    check_restart_storm,
)


def build_report() -> dict:
    results = []
    for fn in CHECKS:
        try:
            results.append(fn())
        except Exception as exc:  # noqa: BLE001 - a gate must not crash the console
            results.append(
                check(fn.__name__.replace("check_", ""), YELLOW, f"check raised: {exc}")
            )
    overall = (
        RED
        if any(r["status"] == RED for r in results)
        else (YELLOW if any(r["status"] == YELLOW for r in results) else GREEN)
    )
    return {
        "gate": "drift",
        "checked_at_epoch": time.time(),
        "overall": overall,
        "ok": overall != RED,
        "checks": results,
    }


def write_atomic(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(
        "w",
        encoding="utf-8",
        dir=path.parent,
        prefix=".drift-gate-",
        suffix=".tmp",
        delete=False,
    ) as h:
        json.dump(payload, h, indent=2)
        h.flush()
        os.fsync(h.fileno())
        tmp = Path(h.name)
    tmp.replace(path)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--print", dest="print_only", action="store_true")
    ap.add_argument("--status-path", type=Path, default=_status_path())
    args = ap.parse_args(argv)

    report = build_report()
    if not args.print_only:
        try:
            write_atomic(args.status_path, report)
        except OSError as exc:
            print(
                f"drift-gate: could not write {args.status_path}: {exc}",
                file=sys.stderr,
            )

    if args.json:
        print(json.dumps(report, indent=2))
    else:
        icon = {GREEN: "OK  ", YELLOW: "WARN", RED: "FAIL"}
        print(f"drift gate: {report['overall'].upper()}")
        for item in report["checks"]:
            print(f"  [{icon[item['status']]}] {item['name']:<20} {item['detail']}")
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
