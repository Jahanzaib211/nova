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


def _resolve_container(name: str) -> str | None:
    """Resolve a container name that may have a compose project prefix."""
    rc, out, _ = _run(["docker", "inspect", name, "--format", "{{.Name}}"], timeout=10)
    if rc == 0 and out:
        return name
    # Try partial name match (compose-prefixed containers)
    rc2, names, _ = _run(
        ["docker", "ps", "--format", "{{.Names}}", "--filter", f"name={name}"],
        timeout=10,
    )
    if rc2 == 0 and names:
        return names.strip().split("\n")[0]
    return None


# Restarts below this are noise regardless of rate -- a couple over an app's
# lifetime says nothing.
PM2_RESTART_MIN = 5

# A container held up this long since its last restart is stable now — its
# cumulative RestartCount is history, not an active loop.
RESTART_STORM_GRACE_MIN = 15

# How long an app must have been up before its cumulative restart count stops
# counting as churn. See check_pm2_apps for why this is a plain uptime floor
# and not a rate.
PM2_STABLE_HOURS = 6.0


def _status_path() -> Path:
    override = os.environ.get("NOVA_DRIFT_GATE_STATUS_PATH", "").strip()
    return Path(override) if override else Path.home() / ".nova" / "gates" / "drift.json"


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
        f"{len(changed)} uncommitted change(s) — running code is not reproducible from any commit",
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
        f"config.yaml is v{live}, config.example.yaml is v{example} — run `make config-upgrade`",
        live=live,
        example=example,
    )


# Hash every file under frontend/src identically on both sides. LC_ALL=C is
# load-bearing: the host sorts with a locale-aware collation and the container
# with C, so `./app/[lang]/...` lands in a different position and the digests
# differ for two byte-identical trees.
_SRC_HASH_CMD = "export LC_ALL=C; cd {path} && find . -type f -print0 | sort -z | xargs -0 sha256sum 2>/dev/null | sha256sum | cut -d' ' -f1"


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
    # Container name may have a compose project prefix (e.g. 48352e221a29_deer-flow-frontend)
    container = _resolve_container("deer-flow-frontend")
    if not container:
        return check("frontend_build", YELLOW, "frontend container not running")
    rc, image, _ = _run(
        ["docker", "inspect", container, "--format", "{{.Config.Image}}"],
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
        ["docker", "inspect", container, "--format", "{{json .Config.Cmd}}"],
        timeout=30,
    )
    is_dev_server = rc_c == 0 and ("next dev" in cmd or "run dev" in cmd)

    rc_m, mounts, _ = _run(
        [
            "docker",
            "inspect",
            container,
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
            "running a dev server but frontend/src is NOT bind-mounted — it is compiling the image's baked copy, so your edits are invisible",
        )

    # `next start` reads .next/BUILD_ID once at boot and refuses to start
    # without it ("Could not find a production build"). An interrupted build
    # leaves the rest of .next rewritten but never writes that file, which is a
    # uniquely nasty state: the already-running server keeps serving from memory
    # while the *next* restart — or reboot — fails outright, and the lazily
    # loaded route chunks it points at have already been deleted from disk, so
    # the workspace dies with a ChunkLoadError while `/` still returns 200 and
    # every HTTP probe stays green. That is not a heuristic; the file is either
    # there or the container cannot come back. Checked before the image-hash
    # comparison below because it holds regardless of how source is delivered.
    build_id_path = REPO_ROOT / "frontend" / ".next" / "BUILD_ID"
    if not build_id_path.is_file():
        return check(
            "frontend_build",
            RED,
            "frontend/.next/BUILD_ID is missing — the last `next build` did not finish, so deer-flow-frontend cannot be restarted and lazily loaded route chunks 500",
        )

    rc_h, host_hash, _ = _run(["bash", "-c", _SRC_HASH_CMD.format(path=str(src))], timeout=120)
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
        # The prod-frontend overlay bind-mounts frontend/ and runs `next start`
        # against a build made on the host, so the image bakes no /app/frontend/src
        # to hash. Reporting only "could not hash" made this check fail *open* on
        # exactly the deployment it exists to protect. Fall back to comparing the
        # build against the source it was built from.
        newest_src = 0.0
        for f in src.rglob("*"):
            if f.is_file():
                try:
                    newest_src = max(newest_src, f.stat().st_mtime)
                except OSError:
                    continue
        built_at = build_id_path.stat().st_mtime
        if newest_src > built_at:
            drift_s = int(newest_src - built_at)
            return check(
                "frontend_build",
                YELLOW,
                f"frontend/src is {drift_s}s newer than build {build_id_path.read_text().strip()} — those edits are not being served; run `pnpm build`",
            )
        return check(
            "frontend_build",
            GREEN,
            f"build {build_id_path.read_text().strip()} is newer than frontend/src (image bakes no source: {err[:60] or 'no /app/frontend/src'})",
        )

    rc_b, build_id, _ = _run(
        ["docker", "exec", container, "cat", "/app/frontend/.next/BUILD_ID"],
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
        f"frontend/src differs from the source baked into the served build ({build}) — those changes are NOT live; rebuild the image",
        build_id=build,
        host_hash=host_hash.strip()[:16],
        image_hash=image_hash.strip()[:16],
    )


def _acp_agents_enabled() -> bool:
    """Mirror scripts/pm2-deerflow.sh: process env first, then `.env`."""
    value = os.environ.get("NOVA_ACP_AGENTS")
    if value is None:
        try:
            for line in (REPO_ROOT / ".env").read_text(encoding="utf-8").splitlines():
                if line.startswith("NOVA_ACP_AGENTS="):
                    value = line.split("=", 1)[1].strip().strip("\"'")
        except OSError:
            value = None
    return value == "1"


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
    # ACP agents (Claude Code + OpenClaw inside Nova) are an opt-in pair of
    # overlays, selected by NOVA_ACP_AGENTS=1 in .env exactly as
    # scripts/pm2-deerflow.sh selects them.
    if _acp_agents_enabled():
        canonical.update({"docker-compose.cli-auth.yaml", "docker-compose.acp.yaml"})

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
        return check("compose_chain", GREEN, f"{len(seen)} overlay(s) match the canonical chain")
    extra, absent = seen - canonical, canonical - seen
    bits = []
    if absent:
        bits.append(f"missing {sorted(absent)}")
    if extra:
        bits.append(f"unexpected {sorted(extra)}")
    return check(
        "compose_chain",
        YELLOW,
        "; ".join(bits) + " — containers were not brought up by scripts/pm2-deerflow.sh",
        seen=sorted(seen),
        canonical=sorted(canonical),
    )


def _launcher_scaled_to_zero() -> set[str]:
    """Services scripts/pm2-deerflow.sh intentionally starts with --scale N=0.

    Parsed from the launcher instead of hardcoded: this set is the difference
    between "a service is down" and "a service was never meant to be up", and a
    copy here would silently rot the moment the launcher changed.
    """
    try:
        text = (REPO_ROOT / "scripts" / "pm2-deerflow.sh").read_text()
    except OSError:
        return set()
    return set(re.findall(r"--scale\s+([A-Za-z0-9_-]+)=0\b", text))


def check_services_running() -> dict:
    """Every service the compose chain declares, actually up?

    ``check_compose_chain`` compares the *files* a container was built from, and
    ``P7_containers`` counts the containers that exist. Neither notices a
    service that is declared and was simply never started -- and that is a real
    failure mode here, because bringing one service up by hand
    (``docker compose up -d gateway``) starts exactly that service and silently
    leaves the rest of the chain alone.

    Services the launcher deliberately scales to zero are not failures. As of
    2026-08-30 ``scripts/pm2-deerflow.sh`` runs with
    ``--scale provisioner=0 --scale searxng=0``, so both are *expected* absent
    and this check must not flag them -- a gate that is permanently red on an
    intentional configuration is one people learn to ignore. The scale-0 set is
    read from that script rather than duplicated here, so the two cannot drift
    apart.
    """
    files = [
        "docker/docker-compose-dev.yaml",
        "docker/docker-compose.dood.yaml",
        "docker/docker-compose.prod-frontend.yaml",
    ]
    args: list[str] = []
    for f in files:
        path = REPO_ROOT / f
        if not path.exists():
            return check("services_running", YELLOW, f"missing compose file {f}")
        args += ["-f", str(path)]

    rc, declared_out, err = _run(["docker", "compose", *args, "config", "--services"])
    if rc != 0:
        return check("services_running", YELLOW, f"cannot read chain: {err[:120]}")
    declared = {line.strip() for line in declared_out.splitlines() if line.strip()}

    rc, running_out, err = _run(["docker", "compose", "-p", "deer-flow-dev", "ps", "--services"])
    if rc != 0:
        return check("services_running", YELLOW, f"cannot list running: {err[:120]}")
    running = {line.strip() for line in running_out.splitlines() if line.strip()}

    expected_down = _launcher_scaled_to_zero()
    missing = sorted(declared - running - expected_down)
    if not missing:
        note = f"all {len(declared - expected_down)} expected services are up"
        if expected_down:
            note += f" ({', '.join(sorted(expected_down))} scaled to 0 by the launcher)"
        return check(
            "services_running",
            GREEN,
            note,
            declared=sorted(declared),
            expected_down=sorted(expected_down),
        )
    return check(
        "services_running",
        RED,
        f"declared but not running: {', '.join(missing)} ({len(running)}/{len(declared)} up) -- bring the stack up with scripts/pm2-deerflow.sh (pm2 restart nova), not a per-service up",
        missing=missing,
        declared=sorted(declared),
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
        running = {a["name"]: a["pm2_env"].get("status") for a in json.loads(out) if a.get("name") in declared}
    except (json.JSONDecodeError, KeyError, TypeError):
        return check("pm2_apps", YELLOW, "could not parse pm2 output")

    # Restart churn, which `check_restart_storm` structurally cannot see: that
    # check reads Docker's RestartCount for the containers, so a PM2 app that
    # is flapping is invisible to it.
    #
    # This deliberately does NOT compute a rate. The obvious form --
    # `restart_time / hours_of_uptime` -- divides two numbers with different
    # epochs: `restart_time` is cumulative and SURVIVES `pm2 resurrect`, while
    # `pm_uptime` (and `created_at`, which resurrect also rewrites) is reset by
    # it. On 2026-09-01 `nova` read 51 restarts against 20.5h of unbroken
    # uptime, giving a fictitious 2.5/h and a standing yellow for a process
    # that had not restarted once in most of a day. There is no epoch on the
    # PM2 side to make that division honest.
    #
    # An app that is flapping has, by definition, restarted RECENTLY -- so ask
    # that directly. Short uptime with restarts behind it is the real signal;
    # a long-stable app is not churning no matter how eventful its history.
    churn = []
    try:
        now_ms = time.time() * 1000
        for a in json.loads(out):
            if a.get("name") not in declared:
                continue
            env = a.get("pm2_env") or {}
            restarts = int(env.get("restart_time") or 0)
            up_h = max((now_ms - float(env.get("pm_uptime") or now_ms)) / 3_600_000, 0.0)
            if restarts >= PM2_RESTART_MIN and up_h < PM2_STABLE_HOURS:
                churn.append(f"{a['name']}={restarts} restarts, last {up_h:.1f}h ago")
    except (json.JSONDecodeError, KeyError, TypeError, ValueError):
        pass

    absent = sorted(declared - set(running))
    stopped = sorted(n for n, st in running.items() if st != "online")
    if not absent and not stopped and not churn:
        return check("pm2_apps", GREEN, f"all {len(declared)} declared apps online")
    bits = []
    if churn:
        bits.append(f"restart churn: {', '.join(churn)}")
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
                "{{.RestartCount}}|{{.State.StartedAt}}|{{.State.OOMKilled}}|{{.Created}}",
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

        def _age_hours(value: str) -> float | None:
            try:
                # Docker emits RFC3339 with nanoseconds, which %f cannot parse.
                cleaned = re.sub(r"\.(\d{6})\d*", r".\1", value.strip()).replace("Z", "+00:00")
                return (_dt.datetime.now(_dt.UTC) - _dt.datetime.fromisoformat(cleaned)).total_seconds() / 3600
            except (ValueError, TypeError):
                return None

        # StartedAt is when the container LAST (re)started; Created is when it
        # was made and RestartCount began at 0. The honest rate divides the
        # cumulative count by time since Created, not since the last restart —
        # otherwise a container that flapped during an outage and then held
        # reads as an infinite storm (208 restarts "in 0.6h") forever, because
        # StartedAt keeps resetting while the count only grows.
        uptime_h = _age_hours(parts[1])
        lifetime_h = _age_hours(parts[3]) if len(parts) > 3 else None

        if oom:
            findings.append(f"{name}: OOM-killed")
            worst = RED
            continue
        if restarts == 0:
            continue

        # An active crash loop keeps restarting, so it can never stay up long:
        # a container held past the grace window is stable *now*, whatever its
        # lifetime count. That is the signal the gate actually wants — autoheal
        # masking a loop that is happening, not the scar of one that is over.
        uptime_min = uptime_h * 60 if uptime_h is not None else None
        stable_now = uptime_min is not None and uptime_min >= RESTART_STORM_GRACE_MIN

        if uptime_min is not None and uptime_min < RESTART_STORM_GRACE_MIN and restarts >= 3:
            findings.append(f"{name}: up only {uptime_min:.0f}m after {restarts} restarts")
            worst = RED
        elif stable_now:
            # Stable. Cumulative churn is history; note a genuinely high
            # lifetime rate as yellow, but a currently-healthy container is
            # never red for restarts it is no longer doing.
            if lifetime_h and lifetime_h > 0 and restarts / lifetime_h >= 2:
                findings.append(f"{name}: {restarts} restarts over {lifetime_h:.1f}h lifetime (stable now, up {uptime_min:.0f}m)")
                worst = YELLOW if worst == GREEN else worst
        elif restarts >= 3:
            # Uptime unreadable; fall back to the lifetime rate.
            if lifetime_h and lifetime_h > 0 and restarts / lifetime_h >= 2:
                findings.append(f"{name}: {restarts} restarts over {lifetime_h:.1f}h")
                worst = RED
            else:
                findings.append(f"{name}: {restarts} restarts")
                worst = YELLOW if worst == GREEN else worst

    if not findings:
        return check("restart_storm", GREEN, "no repeated restarts")
    return check(
        "restart_storm",
        worst,
        "; ".join(findings) + " — autoheal may be masking a crash loop",
    )


def check_gateway_freshness() -> dict:
    """Is the gateway process actually running the backend source on disk?

    The gateway bind-mounts backend/ and runs uvicorn with --reload, which makes
    it feel like edits are always live — so nobody thinks to check. But the
    reloader can wedge: dev-entrypoint.sh bounds graceful shutdown precisely
    because a reload otherwise waits forever on long-lived SSE connections and
    the old worker never exits. When that happens there is no error anywhere;
    the process simply keeps serving code from whenever it last started.

    Found in the wild at a 33-hour drift, while the frontend was independently
    stale — between them the whole subagent panel was dark and every HTTP probe
    was green. Compare the worker's start time against the newest backend source
    file; mtime is the right instrument here because the question is literally
    "did this process start before that edit".
    """
    rc, started_at, _ = _run(
        ["docker", "inspect", "deer-flow-gateway", "--format", "{{.State.StartedAt}}"],
        timeout=30,
    )
    if rc != 0 or not started_at.strip():
        return check("gateway_freshness", YELLOW, "gateway container not running")

    from datetime import datetime

    raw = started_at.strip()
    try:
        # Docker emits more sub-second digits than fromisoformat accepts.
        cleaned = re.sub(r"(\.\d{6})\d+", r"\1", raw.replace("Z", "+00:00"))
        boot = datetime.fromisoformat(cleaned).timestamp()
    except ValueError:
        return check("gateway_freshness", YELLOW, f"unparsable start time: {raw[:40]}")

    app_dir = REPO_ROOT / "backend" / "app"
    if not app_dir.is_dir():
        return check("gateway_freshness", YELLOW, "backend/app not found")

    newest = 0.0
    newest_file = ""
    for f in app_dir.rglob("*.py"):
        try:
            m = f.stat().st_mtime
        except OSError:
            continue
        if m > newest:
            newest, newest_file = m, str(f.relative_to(REPO_ROOT))

    # A reload leaves the container's StartedAt alone, so a container older than
    # the edit is only suspicious, not proof. Confirm against the serving
    # process — but note *which* process that is: uvicorn's --reload supervisor
    # never restarts itself, it re-spawns the app as a multiprocessing child.
    # Matching on "uvicorn app.gateway.app" therefore finds only the supervisor,
    # which is as old as the container and would report a healthy reload as
    # stale. The spawn child is the one whose age answers the question.
    import time

    now = time.time()
    # `docker top` rejects a format without a pid column.
    rc_p, top_out, _ = _run(["docker", "top", "deer-flow-gateway", "-eo", "pid,etimes,args"], timeout=30)
    worker_start = 0.0
    if rc_p == 0:
        for line in top_out.splitlines()[1:]:
            parts = line.split(None, 2)
            if len(parts) != 3 or not parts[1].isdigit():
                continue
            etimes, args = int(parts[1]), parts[2]
            if "multiprocessing.spawn" in args or "uvicorn app.gateway.app" in args:
                worker_start = max(worker_start, now - etimes)

    effective = max(boot, worker_start)
    if newest > effective:
        drift_s = int(newest - effective)
        return check(
            "gateway_freshness",
            RED,
            f"{newest_file} is {drift_s}s newer than the running uvicorn worker — the --reload watcher did not pick it up (wedged reloader); restart deer-flow-gateway",
            newest_file=newest_file,
            drift_seconds=drift_s,
        )
    return check(
        "gateway_freshness",
        GREEN,
        "uvicorn worker is newer than every file in backend/app",
    )


CHECKS = (
    check_git_clean,
    check_config_version,
    check_frontend_build_freshness,
    check_gateway_freshness,
    check_compose_chain,
    check_services_running,
    check_pm2_apps,
    check_restart_storm,
)


def build_report() -> dict:
    results = []
    for fn in CHECKS:
        try:
            results.append(fn())
        except Exception as exc:  # noqa: BLE001 - a gate must not crash the console
            results.append(check(fn.__name__.replace("check_", ""), YELLOW, f"check raised: {exc}"))
    overall = RED if any(r["status"] == RED for r in results) else (YELLOW if any(r["status"] == YELLOW for r in results) else GREEN)
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
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
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
