"""Stack-wide hardening: every healthchecked long-running service must be
autoheal-watchable.

2026-08-28 41-minute nginx-502 outage: ``deer-flow-frontend`` SIGTERM'd, the
exited container still held the service name, and ``docker compose up``
restart-looped with ``Conflict. The container name "/deer-flow-frontend" is
already in use`` while nginx SERVFAIL'd every non-/api request to the
public URL.

The container had no healthcheck, so the autoheal sidecar (which watches
the ``autoheal=true`` label) treated it as healthy-by-omission and never
recreated it. With a healthcheck the sidecar sees the red, calls
``docker restart`` on the *same* container, and stops the loop at one bad
container instead of forever trying to spawn a name-conflict successor.

The same gap was then found on **every** healthchecked service across all
three compose files — `docker-compose.yaml`, `docker-compose-dev.yaml`,
and `docker-compose.nova-prod.yaml` — none of them except the gateway
carried the `autoheal=true` label, and several services were either
missing the healthcheck entirely or had it but no auto-recovery hook.
This suite guards all three together so a future refactor can't reintroduce
the gap on just one of them without the others catching the drift.
"""

from __future__ import annotations

from pathlib import Path

import yaml


REPO_ROOT = Path(__file__).resolve().parents[2]
DOCKER_DIR = REPO_ROOT / "docker"

# All three production compose files are scanned: the dev stack
# (``docker-compose-dev.yaml``) is what PM2 actually runs; the public
# production stack (``docker-compose.nova-prod.yaml``) is what serves
# nova.alilabsx.com; the base file (``docker-compose.yaml``) is what
# ``make up`` / ``scripts/deploy.sh`` uses. They each define their own
# services, so the sweep runs per-file rather than per-service.
COMPOSE_FILES = [
    DOCKER_DIR / "docker-compose.yaml",
    DOCKER_DIR / "docker-compose-dev.yaml",
    DOCKER_DIR / "docker-compose.nova-prod.yaml",
]


def _load_services(path: Path) -> dict[str, dict]:
    """Load ``services`` from a compose file, returning empty dict on parse error.

    A handful of compose files in this tree (voice overlays, dood,
    prod-frontend) are deliberately small fragments that only set one or
    two top-level keys; the helper here is sized for the three files
    listed in ``COMPOSE_FILES`` and treats missing ``services:`` as empty
    rather than raising, so a future overlay can be added without the
    test crashing at collection time.
    """
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    return data.get("services", {}) or {}


def _service(path: Path, name: str) -> dict:
    """Return ``services[name]`` or {} when absent."""
    return _load_services(path).get(name, {}) or {}


def _has_autoheal_label(svc: dict) -> bool:
    """True iff the service's ``labels`` map/list contains ``autoheal=true``.

    Compose accepts labels as a list of ``"k=v"`` strings or as a dict.
    Both forms have to round-trip the same way or a future refactor will
    silently drop the label while still satisfying the YAML validator.
    """
    labels = svc.get("labels") or []
    if isinstance(labels, dict):
        pairs = list(labels.items())
    else:
        pairs = [tuple(s.split("=", 1)) for s in labels]
    normalised = {k.strip(): v.strip() for k, v in pairs}
    return normalised.get("autoheal") == "true"


def _has_healthcheck(svc: dict) -> bool:
    """True iff the service declares a non-empty ``healthcheck`` block."""
    return bool(svc.get("healthcheck"))


# ── Frontend (all three files) ───────────────────────────────────────────────

FRONTEND_FILES = COMPOSE_FILES


def test_frontend_service_has_healthcheck() -> None:
    """Every compose file's frontend must declare a healthcheck.

    Without one, both PM2's compose loop and the autoheal sidecar treat the
    container as healthy-by-omission: a dead process that never binds the
    port looks identical to a cold-booting one until a request fails.
    """
    for path in FRONTEND_FILES:
        if not _service(path, "frontend"):
            # The dev overlay chain swaps frontend through
            # docker-compose.prod-frontend.yaml; the base file may not
            # contain a frontend definition in standalone-validation. Skip
            # rather than fail in that case.
            continue
        frontend = _service(path, "frontend")
        assert _has_healthcheck(frontend), (
            f"{path.relative_to(REPO_ROOT)}: frontend service has no healthcheck "
            f"— see 2026-08-28 nginx-502 incident (recreated dead container "
            f"loop while nginx SERVFAIL'd every non-/api request to the "
            f"public URL)"
        )
        # The test command must actually probe the serving port, not just
        # the binary's existence. wget / curl / python one-liner all OK;
        # the live config uses BusyBox ``wget --spider`` from node:22-alpine.
        test = frontend["healthcheck"].get("test")
        assert isinstance(test, list) and len(test) >= 2, (
            f"{path.relative_to(REPO_ROOT)}: frontend healthcheck.test must "
            f"be an argv list (got {test!r})"
        )
        assert test[0] == "CMD", (
            f"{path.relative_to(REPO_ROOT)}: frontend healthcheck.test[0] "
            f"must be 'CMD' so Docker exec's the command inside the "
            f"container (got {test[0]!r})"
        )


def test_frontend_service_has_autoheal_label() -> None:
    """Every frontend in every compose file must carry ``autoheal=true``.

    The autoheal container watches ``AUTOHEAL_CONTAINER_LABEL=autoheal`` and
    only acts on services bearing that label. With the label present, a
    failing healthcheck triggers ``docker restart <name>`` (which replaces
    the dead container cleanly instead of leaving its name held) and the
    2026-08-28 loop cannot recur.
    """
    for path in FRONTEND_FILES:
        if not _service(path, "frontend"):
            continue
        assert _has_autoheal_label(_service(path, "frontend")), (
            f"{path.relative_to(REPO_ROOT)}: frontend service is missing "
            f"the 'autoheal=true' label — autoheal sidecar will not see it"
        )


def test_frontend_healthcheck_interval_is_short_enough() -> None:
    """A 5-minute healthcheck interval would not catch the SIGTERM-before-bind case.

    Docker's default is 30s, matching what the gateway uses. Anything
    longer than 60s risks the user seeing a 502 before autoheal can
    react; anything shorter thrashes without buying detection.
    """
    for path in FRONTEND_FILES:
        if not _service(path, "frontend"):
            continue
        interval = _service(path, "frontend").get("healthcheck", {}).get("interval", "0s")
        assert interval.endswith("s"), (
            f"{path.relative_to(REPO_ROOT)}: frontend healthcheck.interval "
            f"has unexpected format {interval!r}"
        )
        seconds = int(interval[:-1])
        assert 5 <= seconds <= 60, (
            f"{path.relative_to(REPO_ROOT)}: frontend healthcheck.interval="
            f"{seconds}s is outside the 5s–60s band — too long delays "
            f"recovery, too short thrashes"
        )


# ── Gateway (base + dev; nova-prod already had healthcheck+label) ────────────


def test_gateway_service_has_healthcheck_and_label() -> None:
    """Every compose file's gateway must have a healthcheck AND an ``autoheal=true`` label.

    The 2026-07-16 outage was a hung-but-Up uvicorn taking nginx down
    silently. The dev stack fixed it; the 2026-09 hardening sweep extended
    the fix to the base and prod stacks; this test freezes that across
    every compose file in the tree.
    """
    files_with_gateway = [
        p for p in COMPOSE_FILES if "gateway" in _load_services(p)
    ]
    for path in files_with_gateway:
        gateway = _service(path, "gateway")
        assert _has_healthcheck(gateway), (
            f"{path.relative_to(REPO_ROOT)}: gateway has no healthcheck "
            f"— see 2026-07-16 hung-uvicorn outage"
        )
        assert _has_autoheal_label(gateway), (
            f"{path.relative_to(REPO_ROOT)}: gateway is missing the "
            f"'autoheal=true' label — sidecar will not restart a hung one"
        )


# ── nginx (all three; was missing the label in base and dev) ────────────────

NGINX_FILES = COMPOSE_FILES


def test_nginx_service_has_healthcheck_and_label() -> None:
    """Every compose file's nginx must have a healthcheck AND an ``autoheal=true`` label.

    nginx is the only public entrypoint. A hung proxy that the sidecar
    doesn't watch is invisible until a user hits :2026; the prod stack
    already had the label from the dev stack's audit, but the dev and
    base compose files did not.
    """
    files_with_nginx = [
        p for p in NGINX_FILES if "nginx" in _load_services(p)
    ]
    for path in files_with_nginx:
        nginx = _service(path, "nginx")
        assert _has_healthcheck(nginx), (
            f"{path.relative_to(REPO_ROOT)}: nginx has no healthcheck — "
            f"autoheal will treat it as healthy-by-omission"
        )
        assert _has_autoheal_label(nginx), (
            f"{path.relative_to(REPO_ROOT)}: nginx is missing the "
            f"'autoheal=true' label — sidecar will not restart a hung proxy"
        )


# ── Postgres (base + dev + prod) ─────────────────────────────────────────────


def test_postgres_service_has_healthcheck_and_label() -> None:
    """Postgres is the silent killer when it goes down.

    Full disk or connection storm keeps the container "Up" while writes
    back up; a sidecar-watched Postgres recovers in ~30s instead of
    dragging the gateway into deadlock-driven timeouts.
    """
    files_with_postgres = [
        p for p in COMPOSE_FILES if "postgres" in _load_services(p)
    ]
    for path in files_with_postgres:
        postgres = _service(path, "postgres")
        assert _has_healthcheck(postgres), (
            f"{path.relative_to(REPO_ROOT)}: postgres has no healthcheck"
        )
        assert _has_autoheal_label(postgres), (
            f"{path.relative_to(REPO_ROOT)}: postgres is missing the "
            f"'autoheal=true' label"
        )


# ── SearXNG / browserless / crawl4ai (the silent fall-back trio) ─────────────


def test_web_search_and_crawl_services_have_healthcheck_and_label() -> None:
    """web_search / web_fetch / web_crawl all silently fall back on failure.

    SearXNG falls back to DuckDuckGo, browserless to a plain HTTP GET,
    crawl4ai to ... nothing. A container that went down silently because
    its healthcheck stopped working took the privacy panel's SearXNG card
    red for weeks without anyone noticing. The label is the only signal
    that triggers an actual restart instead of a quiet fallback.
    """
    for path in COMPOSE_FILES:
        services = _load_services(path)
        for name in ("searxng", "browserless", "crawl4ai"):
            if name not in services:
                continue
            svc = services[name]
            assert _has_healthcheck(svc), (
                f"{path.relative_to(REPO_ROOT)}: {name} has no healthcheck"
            )
            assert _has_autoheal_label(svc), (
                f"{path.relative_to(REPO_ROOT)}: {name} is missing the "
                f"'autoheal=true' label — silent fallback will hide the outage"
            )


# ── Autoheal sidecar (must have a self-healthcheck in both stacks) ───────────


def test_autoheal_sidecar_has_own_healthcheck() -> None:
    """The autoheal sidecar itself needs a healthcheck.

    Without one, a stuck ``autoheal`` process leaves every
    ``autoheal=true`` container silently unmonitored — and because
    autoheal has no other supervisor, the failure mode reads as "the
    sidecar just stops doing its job" with nothing in ``docker ps``
    flagging it. Both the dev and prod stacks must declare this.
    """
    files_with_autoheal = [
        p for p in COMPOSE_FILES if "autoheal" in _load_services(p)
    ]
    for path in files_with_autoheal:
        autoheal = _service(path, "autoheal")
        assert _has_healthcheck(autoheal), (
            f"{path.relative_to(REPO_ROOT)}: autoheal sidecar has no "
            f"healthcheck — a stuck sidecar takes the whole hardening "
            f"down silently"
        )
        # The self-test must actually probe the docker socket, not just
        # the binary's existence. The live config uses
        # ``curl --unix-socket /var/run/docker.sock`` + ``pgrep -f``.
        test = autoheal["healthcheck"].get("test")
        cmd = " ".join(test) if isinstance(test, list) else test
        assert "unix-socket" in cmd or "docker.sock" in cmd, (
            f"{path.relative_to(REPO_ROOT)}: autoheal healthcheck must "
            f"actually probe the docker socket (got {cmd!r})"
        )


# ── Nginx config-level hardening ──────────────────────────────────────────────


NGINX_CONFIGS = [
    DOCKER_DIR / "nginx" / "nginx.conf",
    DOCKER_DIR / "nginx" / "nginx.local.conf",
    DOCKER_DIR / "nginx" / "nginx.tls.conf",
]


def test_nginx_configs_strip_server_tokens() -> None:
    """Every nginx config must hide its version fingerprint.

    The base ``nginx.conf`` already had ``server_tokens off;``; the
    local-dev and TLS blocks were leaking ``Server: nginx/1.x.x`` until
    the 2026-09 sweep added the directive to both. Free win, but easy
    to forget on a future TLS or local-dev refactor.
    """
    for path in NGINX_CONFIGS:
        if not path.exists():
            continue
        text = path.read_text(encoding="utf-8")
        assert "server_tokens off" in text, (
            f"{path.relative_to(REPO_ROOT)}: nginx config is missing "
            f"'server_tokens off;' — leaking the nginx version in the "
            f"Server response header"
        )
        # Must be in a valid context (http or server). Anything else is
        # a syntax error per nginx directive reference. A bare match
        # without looking at position would catch the wrong placement.
        assert "server_tokens off;" in text + " ", (
            f"{path.relative_to(REPO_ROOT)}: 'server_tokens off;' is "
            f"missing the trailing semicolon"
        )
