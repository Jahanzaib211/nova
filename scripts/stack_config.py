"""Read-only helpers for the stack's declared supervision config.

Shared by the host-side producers (gates, inventory, watchdog) so they agree
on one answer to "what is this deployment supposed to be running?". Stdlib
only: these scripts run under the system python, before any venv exists.

The pm2 ecosystem file is JavaScript. Evaluating it with node would be exact,
but node is not guaranteed on every producer's PATH and a hung node would
stall a gate; the file is plain data in practice, so it is read as text — with
comments stripped first, because a commented-out app (one switched off on
purpose, e.g. while its venv is missing) must not count as declared.
"""

from __future__ import annotations

import os
import re
import subprocess
from collections.abc import Mapping
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent

# Cloudflare Tunnel connector defaults. Every value is overridable by env, so
# a deployment that runs cloudflared differently configures it rather than
# editing code. The metrics server's /ready answers 200 only while the
# connector holds at least one edge connection, so it is the most direct
# liveness signal when it is enabled (`cloudflared tunnel --metrics ...`).
DEFAULT_TUNNEL_UNITS = "cloudflared.service,cloudflared-nova.service"
DEFAULT_TUNNEL_PM2_APPS = "tunnel-nova"
DEFAULT_TUNNEL_METRICS_URLS = "http://127.0.0.1:20241/ready"


# ── Compose project / container naming ────────────────────────────────────
# The stack was renamed from `deer-flow-dev` / `deer-flow-<svc>` to `nova` /
# `nova-<svc>` (scripts/migrate-deerflow-to-nova.sh does the operator-run
# cutover). Producers must keep working on BOTH sides of that cutover — a gate
# that goes red the moment the code lands, before the operator has migrated,
# is a false alarm — so everything here tries the new name first and falls
# back to the legacy one. NOVA_COMPOSE_PROJECT pins the project explicitly.
COMPOSE_PROJECT = "nova"
LEGACY_COMPOSE_PROJECT = "deer-flow-dev"
CONTAINER_PREFIX = "nova-"
LEGACY_CONTAINER_PREFIX = "deer-flow-"


def compose_projects(env: Mapping[str, str] | None = None) -> list[str]:
    """Candidate compose project names, preferred first."""
    env = os.environ if env is None else env
    explicit = (env.get("NOVA_COMPOSE_PROJECT") or "").strip()
    if explicit:
        return [explicit]
    return [COMPOSE_PROJECT, LEGACY_COMPOSE_PROJECT]


def container_candidates(name: str) -> list[str]:
    """``gateway`` / ``nova-gateway`` / ``deer-flow-gateway`` → both container names, new first."""
    service = name
    for prefix in (CONTAINER_PREFIX, LEGACY_CONTAINER_PREFIX):
        if name.startswith(prefix):
            service = name[len(prefix) :]
            break
    return [CONTAINER_PREFIX + service, LEGACY_CONTAINER_PREFIX + service]


def _docker_ok(cmd: list[str]) -> str | None:
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=10)
    except (OSError, subprocess.SubprocessError):
        return None
    if proc.returncode != 0:
        return None
    return proc.stdout if isinstance(proc.stdout, str) else ""


def resolve_container(name: str) -> str:
    """The container that actually exists for ``name`` (new name, else legacy).

    Returns the new name when neither exists (or docker is unavailable), so
    callers report the name the stack is supposed to use.
    """
    candidates = container_candidates(name)
    for candidate in candidates:
        if _docker_ok(["docker", "inspect", "--format", "{{.Name}}", candidate]) is not None:
            return candidate
    return candidates[0]


def resolve_compose_project(env: Mapping[str, str] | None = None) -> str:
    """The compose project that owns containers on this host (new, else legacy)."""
    candidates = compose_projects(env)
    if len(candidates) == 1:
        return candidates[0]
    for project in candidates:
        out = _docker_ok(["docker", "ps", "-a", "-q", "--filter", f"label=com.docker.compose.project={project}"])
        if out and out.strip():
            return project
    return candidates[0]


def _csv(raw: str) -> list[str]:
    return [part.strip() for part in raw.split(",") if part.strip()]


def tunnel_units(env: Mapping[str, str] | None = None) -> list[str]:
    """systemd units that may host the tunnel connector (HEALTHCHECK_TUNNEL_UNITS)."""
    env = os.environ if env is None else env
    return _csv(env.get("HEALTHCHECK_TUNNEL_UNITS", DEFAULT_TUNNEL_UNITS))


def tunnel_pm2_apps(env: Mapping[str, str] | None = None) -> list[str]:
    """pm2 apps that may host the tunnel connector (HEALTHCHECK_TUNNEL_PM2_APPS)."""
    env = os.environ if env is None else env
    return _csv(env.get("HEALTHCHECK_TUNNEL_PM2_APPS", DEFAULT_TUNNEL_PM2_APPS))


def tunnel_metrics_urls(env: Mapping[str, str] | None = None) -> list[str]:
    """cloudflared metrics /ready URLs (HEALTHCHECK_TUNNEL_METRICS_URLS; empty disables)."""
    env = os.environ if env is None else env
    return _csv(env.get("HEALTHCHECK_TUNNEL_METRICS_URLS", DEFAULT_TUNNEL_METRICS_URLS))


def ecosystem_path() -> Path:
    """The pm2 ecosystem file — same NOVA_ECOSYSTEM_FILE override the watchdog honours."""
    return Path(os.environ.get("NOVA_ECOSYSTEM_FILE") or (REPO_ROOT / "ecosystem.config.js"))


def strip_js_comments(src: str) -> str:
    """Remove ``//`` and ``/* */`` comments, leaving string literals intact.

    A small scanner rather than a regex so a ``//`` inside a string (a URL) or
    a quote inside a comment ("don't") cannot derail it. Newlines inside block
    comments are kept so line numbers still line up for anyone debugging.
    Regex literals are not recognised; the ecosystem file has none.
    """
    out: list[str] = []
    i, n = 0, len(src)
    quote: str | None = None
    while i < n:
        ch = src[i]
        if quote:
            out.append(ch)
            if ch == "\\" and i + 1 < n:
                out.append(src[i + 1])
                i += 2
                continue
            if ch == quote:
                quote = None
            i += 1
            continue
        if ch in "\"'`":
            quote = ch
            out.append(ch)
            i += 1
            continue
        if src.startswith("//", i):
            end = src.find("\n", i)
            i = n if end == -1 else end
            continue
        if src.startswith("/*", i):
            end = src.find("*/", i + 2)
            block = src[i : n if end == -1 else end + 2]
            out.append("\n" * block.count("\n"))
            i = n if end == -1 else end + 2
            continue
        out.append(ch)
        i += 1
    return "".join(out)


def read_ecosystem(path: Path | None = None) -> str | None:
    """The ecosystem file with comments stripped; None when unreadable."""
    try:
        return strip_js_comments((path or ecosystem_path()).read_text(encoding="utf-8"))
    except OSError:
        return None


_APP_NAME = re.compile(r"""\bname:\s*["']([^"']+)["']""")


def declared_pm2_apps(stripped_src: str) -> list[str]:
    """App names declared (not commented out) in an already-stripped ecosystem source."""
    seen: list[str] = []
    for name in _APP_NAME.findall(stripped_src):
        if name not in seen:
            seen.append(name)
    return seen


def ecosystem_env_value(stripped_src: str, key: str) -> str | None:
    """First ``KEY: "value"`` in the stripped source (the value may sit on the next line)."""
    m = re.search(rf"""\b{re.escape(key)}:\s*(["'])(.*?)\1""", stripped_src, re.DOTALL)
    return m.group(2) if m else None


def disabled_probes(env: Mapping[str, str] | None = None, stripped_src: str | None = None) -> set[str]:
    """Watchdog probes switched off for this deployment.

    HEALTHCHECK_DISABLED_PROBES from the environment wins (that is what the
    watchdog itself reads); otherwise the value nova-healthcheck is given in
    the ecosystem file, so a producer running under a different pm2 app (the
    gates have their own env) still honours the same switch.
    """
    env = os.environ if env is None else env
    raw = env.get("HEALTHCHECK_DISABLED_PROBES")
    if raw is None:
        src = read_ecosystem() if stripped_src is None else stripped_src
        raw = ecosystem_env_value(src or "", "HEALTHCHECK_DISABLED_PROBES") or ""
    return set(_csv(raw))
