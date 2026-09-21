#!/usr/bin/env python3
"""Regression gate: everything Nova has shipped, probed from its sources of truth.

Writes ~/.nova/gates/regression.json. The point is not one more health check
but a *ledger*: each inventory is enumerated from where it is declared, and
the counts are pinned in ``contracts/regression.baseline.json`` so a
capability can only disappear by editing the baseline on purpose. The
2026-09-20 loss of the sandbox image (every self-probe and gate said
"healthy" while the Agent's Computer could not start) is the failure this
exists to catch.

Sections (each a check; red/yellow/green):

- ``overlays``      compose overlays the PM2 chain must be running, from
                    scripts/pm2-deerflow.sh's rules (.env NOVA_ACP_AGENTS,
                    config.yaml speech) vs the containers' config_files labels.
- ``sandbox_image`` config.yaml `sandbox.image` exists on the daemon and boots;
                    its /etc/nova-sandbox.json manifest is readable.
- ``sandbox_tools`` the toolchains and the security arsenal Dockerfile.tools
                    and Dockerfile.android install are present in the image
                    (`command -v` inside `docker run --rm`).
- ``security_toolkit`` the knowledge base mount (DEER_FLOW_SECURITY_TOOLKIT)
                    exists on the host.
- ``backend_extras`` the harness extras (postgres, voice, trading) import in
                    the gateway container — `uv sync` silently strips them.
- ``config_prereqs`` every enabled config.yaml section has what it needs
                    (speech weights, ACP mounts, jobs worker, runtimes adapters).
- ``services``      PM2 apps online, tunnels online, local LLM endpoints,
                    nova-ops, Postgres, frontend served build == image build.
- ``e2e_toolchain`` Playwright's Chromium build for the pinned playwright-core.
- ``inventory_counts`` skills / config tools / builtin tools / capability ops /
                    MCP servers / sandbox tools vs the baseline (never lower).

    python3 scripts/gates/regression-gate.py            # write + summary
    python3 scripts/gates/regression-gate.py --json     # echo the document
    python3 scripts/gates/regression-gate.py --write-baseline   # pin today's counts
"""

from __future__ import annotations

import argparse
import json
import os
import re
import secrets
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
BASELINE = REPO_ROOT / "contracts" / "regression.baseline.json"
GREEN, YELLOW, RED = "green", "yellow", "red"

# Binaries Dockerfile.tools / Dockerfile.android install into the sandbox.
# SOURCE OF TRUTH: docker/sandbox/verify-toolchain.sh's CHECKS array.
# This dictionary MUST match that script — a tool here that the image does
# not ship is a false green; a tool the image ships that is missing here
# is a missed regression. Grouped by the Dockerfile's install sections.
SANDBOX_TOOLCHAIN = {
    # Security arsenal (mapped 1:1 to Security-Toolkit domain cheatsheets)
    "network": ["nmap", "masscan", "tshark"],
    "vulnscan": ["nuclei", "trivy", "nikto", "wapiti", "whatweb"],
    "webapp": ["sqlmap", "gobuster", "ffuf", "dirb", "dalfox"],
    "secrets": ["gitleaks", "trufflehog", "ropper", "detect-secrets"],
    "osint": ["subfinder", "httpx", "amass", "dnsrecon", "sherlock", "arjun"],
    "creds": ["hydra", "john", "hashcat", "smbclient", "secretsdump.py"],
    "forensics": ["binwalk", "foremost", "exiftool", "yara", "fls", "sslscan"],
    "wireless": ["aircrack-ng", "bettercap", "wifite"],
    "blueteam": ["suricata", "lynis", "chkrootkit"],
    # Software-house completeness
    "software_house": ["gh", "ansible", "sqlite3", "strace", "ltrace",
                       "composer", "php", "ruby", "dotnet", "pre-commit"],
    # Languages and runtimes
    "languages": ["python3", "node", "npm", "go", "rustc", "cargo", "uv", "python"],
    # Dev tools and document processing
    "dev": ["pandoc", "wkhtmltopdf", "tesseract", "psql", "redis-cli",
            "jq", "fd", "bat", "rg", "figlet", "soffice", "pdftotext",
            "pdfinfo", "qpdf", "unoconv", "fzf", "httpie", "cwebp", "magick",
            "playwright", "chromium"],
    # Cloud CLIs (static Go binaries)
    "cloud": ["kubectl", "helm", "terraform"],
}

# Flattened set for quick lookup — all tools across all groups.
_ALL_SANDBOX_TOOLS: set[str] = set()
for _tools in SANDBOX_TOOLCHAIN.values():
    _ALL_SANDBOX_TOOLS.update(_tools)

# Host-side security tooling the README lists as host-only (Metasploit, IDS,
# hardening). Reported, never red: the host is not built from this repo.
HOST_SECURITY_TOOLS = ["msfconsole", "suricata", "snort", "lynis", "chkrootkit", "nmap", "semgrep", "trivy", "gitleaks", "trufflehog", "grype", "nuclei"]

HARNESS_EXTRAS = {
    "postgres": ["asyncpg", "psycopg", "langgraph.checkpoint.postgres"],
    "voice": ["faster_whisper", "kokoro_onnx", "onnxruntime"],
    "trading": ["ccxt", "yfinance"],
}

NOVA_PM2_APPS = ["nova", "nova-litellm", "nova-healthcheck", "nova-gates", "nova-ops", "nova-host-bridge", "tunnel-nova"]


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


def section_enabled(text: str, section: str) -> bool:
    m = re.search(rf"^{section}:\s*$\s+enabled:\s*(\w+)", text, re.MULTILINE)
    return bool(m and m.group(1).lower() == "true")


def config_value(text: str, section: str, key: str) -> str | None:
    m = re.search(rf"^{section}:\s*$(?:\n[ \t]+.*)*?\n[ \t]+{key}:\s*([^\s#]+)", text, re.MULTILINE)
    return m.group(1) if m else None


# ---------------------------------------------------------------- overlays


def expected_overlays(text: str) -> set[str]:
    expected = {"docker-compose-dev.yaml", "docker-compose.dood.yaml", "docker-compose.prod-frontend.yaml"}
    if section_enabled(text, "speech"):
        expected.add("docker-compose.voice.yaml")
    if read_env("NOVA_ACP_AGENTS") == "1":
        expected.update({"docker-compose.cli-auth.yaml", "docker-compose.acp.yaml"})
    return expected


def running_overlays() -> set[str]:
    rc, out = run(["docker", "ps", "--filter", "label=com.docker.compose.project=deer-flow-dev", "--format", '{{.Label "com.docker.compose.project.config_files"}}'])
    seen: set[str] = set()
    if rc == 0:
        for line in out.splitlines():
            for f in line.split(","):
                f = f.strip()
                if f:
                    seen.add(os.path.basename(f))
    return seen


def evaluate_overlays(expected: set[str], seen: set[str]) -> dict:
    missing = sorted(expected - seen)
    if not seen:
        return check("overlays", RED, "no deer-flow-dev containers running", expected=sorted(expected), seen=[])
    if missing:
        return check("overlays", RED, "overlay(s) declared by scripts/pm2-deerflow.sh are not in the running chain: " + ", ".join(missing), expected=sorted(expected), seen=sorted(seen), missing=missing)
    return check("overlays", GREEN, f"{len(expected)} overlay(s) running as declared", expected=sorted(expected), seen=sorted(seen), missing=[])


# ---------------------------------------------------------------- sandbox


def sandbox_image(text: str) -> str | None:
    return config_value(text, "sandbox", "image")


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


def evaluate_sandbox_image(image: str | None, exists: bool, manifest: dict | None) -> dict:
    if not image:
        return check("sandbox_image", RED, "config.yaml has no sandbox.image")
    if not exists:
        return check("sandbox_image", RED, f"{image} is not on the Docker daemon — the Agent's Computer cannot start (make sandbox-image)", image=image)
    if manifest is None:
        return check("sandbox_image", YELLOW, f"{image} exists but /etc/nova-sandbox.json is unreadable (pre-manifest build?)", image=image)
    return check("sandbox_image", GREEN, f"{image} layer={manifest.get('layer')} build={manifest.get('build_id')}", image=image, layer=manifest.get("layer"), build_id=manifest.get("build_id"))


def sandbox_missing_tools(image: str, groups: dict[str, list[str]]) -> dict[str, list[str]]:
    """Check all tools in the image. Uses the full inventory including extended tools."""
    names = sorted({b for tools in groups.values() for b in tools})
    script = "for b in " + " ".join(names) + "; do command -v $b >/dev/null 2>&1 || echo MISSING:$b; done"
    rc, out = run(["docker", "run", "--rm", "--entrypoint", "sh", image, "-lc", script], timeout=180)
    missing = {line.split(":", 1)[1] for line in out.splitlines() if line.startswith("MISSING:")}
    return {g: [b for b in tools if b in missing] for g, tools in groups.items()}


def sandbox_manifest_tools(image: str) -> dict[str, str]:
    """Read the tool inventory from the image's /etc/nova-sandbox.json manifest."""
    rc, out = run(["docker", "run", "--rm", "--entrypoint", "cat", image, "/etc/nova-sandbox.json"], timeout=60)
    if rc != 0:
        return {}
    try:
        manifest = json.loads(out)
        return manifest.get("toolchain", {})
    except (ValueError, KeyError):
        return {}


def evaluate_sandbox_tools(image_ok: bool, layer: str | None, missing_by_group: dict[str, list[str]], manifest_tool_count: int = 0) -> dict:
    if not image_ok:
        return check("sandbox_tools", RED, "no sandbox image to inspect")
    groups = dict(missing_by_group)
    if layer != "android":
        groups.pop("android", None)  # a lower rung is a deliberate choice
    missing = {g: v for g, v in groups.items() if v}
    total_from_groups = sum(len(v) for v in SANDBOX_TOOLCHAIN.values())
    # Use manifest count if available (more accurate than hardcoded groups)
    total = manifest_tool_count if manifest_tool_count > 0 else total_from_groups
    if missing:
        return check("sandbox_tools", YELLOW, "missing in the sandbox image: " + "; ".join(f"{g}: {', '.join(v)}" for g, v in missing.items()), missing=missing)
    return check("sandbox_tools", GREEN, f"all {total} toolchain/arsenal binaries present (layer {layer})", missing={})


def evaluate_security_toolkit(path: str | None) -> dict:
    if not path:
        return check("security_toolkit", YELLOW, "DEER_FLOW_SECURITY_TOOLKIT not set in config.yaml sandbox.env")
    p = Path(path)
    if not p.is_dir():
        return check("security_toolkit", RED, f"{path} missing on host — every sandbox mounts it read-only at /mnt/security-toolkit")
    n = sum(1 for _ in p.rglob("*.md"))
    return check("security_toolkit", GREEN, f"{path}: {n} knowledge files", files=n)


# ---------------------------------------------------------------- backend extras


def gateway_import_failures(extras: dict[str, list[str]]) -> dict[str, list[str]]:
    mods = [m for ms in extras.values() for m in ms]
    code = "import importlib,sys\n" + "\n".join(f"\ntry:\n    importlib.import_module({m!r})\nexcept Exception as e:\n    print('FAIL:{m}')" for m in mods)
    rc, out = run(["docker", "exec", "deer-flow-gateway", "/app/backend/.venv/bin/python", "-c", code], timeout=120)
    if rc != 0 and "FAIL:" not in out:
        return {"_": ["gateway container unreachable"]}
    failed = {line.split(":", 1)[1] for line in out.splitlines() if line.startswith("FAIL:")}
    return {e: [m for m in ms if m in failed] for e, ms in extras.items()}


def evaluate_backend_extras(failures: dict[str, list[str]]) -> dict:
    if failures.get("_"):
        return check("backend_extras", YELLOW, failures["_"][0])
    bad = {e: v for e, v in failures.items() if v}
    if bad:
        return check("backend_extras", RED, "harness extras missing in the gateway venv (uv sync strips them; re-sync with --extra): " + "; ".join(f"{e}: {', '.join(v)}" for e, v in bad.items()), missing=bad)
    return check("backend_extras", GREEN, "postgres, voice and trading extras import in the gateway", missing={})


# ---------------------------------------------------------------- config prerequisites


def evaluate_config_prereqs(text: str, facts: dict) -> dict:
    problems: list[str] = []
    if section_enabled(text, "speech") and not facts.get("voice_weights"):
        problems.append("speech.enabled but voice weights dir is missing (voice overlay is skipped)")
    if section_enabled(text, "jobs") and not facts.get("jobs_worker"):
        problems.append("jobs.enabled but no worker heartbeat")
    if read_env("NOVA_ACP_AGENTS") == "1" and not facts.get("acp_mounts"):
        problems.append("NOVA_ACP_AGENTS=1 but /root/.claude or /run/nova/openclaw_token is not mounted in the gateway")
    if section_enabled(text, "runtimes") and not facts.get("acp_ready"):
        problems.append("runtimes.enabled but no ACP runtime reports a ready account")
    if section_enabled(text, "email_marketing") and not read_env("NOVA_EM_TRACKING_SECRET"):
        problems.append("email_marketing.enabled but NOVA_EM_TRACKING_SECRET is unset")
    if problems:
        return check("config_prereqs", RED, "; ".join(problems), problems=problems)
    return check("config_prereqs", GREEN, "every enabled section has its prerequisites", problems=[])


# ---------------------------------------------------------------- services


def http_status(url: str, timeout: float = 5.0, headers: dict | None = None) -> int | None:
    req = urllib.request.Request(url, headers=headers or {})
    try:
        with urllib.request.build_opener(urllib.request.ProxyHandler({})).open(req, timeout=timeout) as resp:  # noqa: S310
            return resp.status
    except urllib.error.HTTPError as exc:
        return exc.code
    except (urllib.error.URLError, OSError):
        return None


def pm2_status() -> dict[str, str]:
    rc, out = run(["pm2", "jlist"], timeout=30)
    if rc != 0:
        return {}
    try:
        return {p["name"]: p["pm2_env"]["status"] for p in json.loads(out)}
    except (ValueError, KeyError, TypeError):
        return {}


def evaluate_services(pm2: dict[str, str], endpoints: dict[str, int | None], served_build: str | None, image_build: str | None) -> dict:
    problems: list[str] = []
    offline = [a for a in NOVA_PM2_APPS if pm2.get(a) != "online"]
    if offline:
        problems.append("PM2 not online: " + ", ".join(f"{a}={pm2.get(a, 'absent')}" for a in offline))
    down = [name for name, st in endpoints.items() if st is None or st >= 500]
    if down:
        problems.append("unreachable: " + ", ".join(f"{n} ({endpoints[n]})" for n in down))
    if served_build and image_build and served_build != image_build:
        problems.append(f"frontend serves build {served_build} but the image has {image_build} (recreate the container)")
    if not served_build:
        problems.append("frontend container not found or its BUILD_ID unreadable")
    if problems:
        return check("services", RED if offline or down else YELLOW, "; ".join(problems), pm2=pm2, endpoints=endpoints)
    return check("services", GREEN, f"{len(NOVA_PM2_APPS)} PM2 apps online, {len(endpoints)} endpoints answering, frontend build {served_build}", pm2={a: pm2.get(a) for a in NOVA_PM2_APPS}, endpoints=endpoints)


# ---------------------------------------------------------------- e2e toolchain


def playwright_expected_revision() -> str | None:
    for f in (REPO_ROOT / "frontend" / "node_modules" / ".pnpm").glob("playwright-core@*/node_modules/playwright-core/browsers.json"):
        try:
            data = json.loads(f.read_text(encoding="utf-8"))
            for b in data.get("browsers", []):
                if b.get("name") == "chromium":
                    return str(b.get("revision"))
        except (OSError, ValueError):
            continue
    return None


def evaluate_e2e_toolchain(revision: str | None, cache: Path) -> dict:
    if revision is None:
        return check("e2e_toolchain", YELLOW, "frontend/node_modules not installed; cannot read playwright-core's browsers.json")
    have = (cache / f"chromium-{revision}").exists() or (cache / f"chromium_headless_shell-{revision}").exists()
    if not have:
        return check("e2e_toolchain", YELLOW, f"Playwright chromium revision {revision} not in {cache} — e2e/visual/lighthouse cannot run locally (symlink a cached build, see frontend/CLAUDE.md)", revision=revision)
    return check("e2e_toolchain", GREEN, f"Playwright chromium {revision} present", revision=revision)


# ---------------------------------------------------------------- inventory counts


def evaluate_counts(current: dict[str, int], baseline: dict[str, int] | None) -> dict:
    if baseline is None:
        return check("inventory_counts", YELLOW, "no contracts/regression.baseline.json — run with --write-baseline", current=current)
    lower = {k: (baseline[k], current.get(k)) for k in baseline if current.get(k) is not None and current[k] < baseline[k]}
    if lower:
        return check("inventory_counts", RED, "below baseline: " + ", ".join(f"{k} {b}->{c}" for k, (b, c) in lower.items()), current=current, baseline=baseline)
    higher = [k for k in baseline if current.get(k, 0) > baseline[k]]
    detail = ", ".join(f"{k}={v}" for k, v in sorted(current.items()))
    if higher:
        detail += " (above baseline: " + ", ".join(higher) + " — refresh it)"
    return check("inventory_counts", GREEN, detail, current=current, baseline=baseline)


# ---------------------------------------------------------------- report


def build_report(checks: list[dict]) -> dict:
    overall = RED if any(c["status"] == RED for c in checks) else (YELLOW if any(c["status"] == YELLOW for c in checks) else GREEN)
    return {"gate": "regression", "checked_at_epoch": time.time(), "overall": overall, "ok": overall != RED, "checks": checks}


def write_atomic(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile("w", encoding="utf-8", dir=path.parent, prefix=".regression-gate-", suffix=".tmp", delete=False) as handle:
        json.dump(payload, handle, indent=2)
        handle.flush()
        os.fsync(handle.fileno())
        tmp = Path(handle.name)
    tmp.replace(path)


# ---------------------------------------------------------------- live collection


def ops_headers() -> dict:
    token = read_env("NOVA_OPS_TOKEN") or ""
    csrf = secrets.token_hex(24)
    return {"X-Nova-Ops-Token": token, "X-CSRF-Token": csrf, "Cookie": f"csrf_token={csrf}", "Content-Type": "application/json"}


def ops_call(base: str, name: str) -> dict | None:
    req = urllib.request.Request(f"{base}/api/capabilities/ops/{name}", data=b"{}", method="POST", headers=ops_headers())
    try:
        with urllib.request.build_opener(urllib.request.ProxyHandler({})).open(req, timeout=20) as resp:  # noqa: S310
            return json.loads(resp.read().decode("utf-8")).get("result")
    except (urllib.error.URLError, OSError, ValueError):
        return None


def collect(base: str) -> tuple[list[dict], dict[str, int]]:
    text = config_text()
    checks: list[dict] = []

    checks.append(evaluate_overlays(expected_overlays(text), running_overlays()))

    image = sandbox_image(text)
    exists = bool(image) and image_exists(image)
    manifest = sandbox_manifest(image) if exists else None
    checks.append(evaluate_sandbox_image(image, exists, manifest))
    missing = sandbox_missing_tools(image, SANDBOX_TOOLCHAIN) if exists else {}
    manifest_tools = sandbox_manifest_tools(image) if exists else {}
    checks.append(evaluate_sandbox_tools(exists, (manifest or {}).get("layer"), missing, len(manifest_tools)))
    checks.append(
        evaluate_security_toolkit(config_value(text, "sandbox", "DEER_FLOW_SECURITY_TOOLKIT") or re.search(r"DEER_FLOW_SECURITY_TOOLKIT:\s*(\S+)", text).group(1) if re.search(r"DEER_FLOW_SECURITY_TOOLKIT:\s*(\S+)", text) else None)
    )

    checks.append(evaluate_backend_extras(gateway_import_failures(HARNESS_EXTRAS)))

    rc, mounts = run(["docker", "inspect", "deer-flow-gateway", "--format", "{{range .Mounts}}{{.Destination}} {{end}}"], timeout=30)
    acp_mounts = rc == 0 and "/root/.claude" in mounts and "/run/nova/openclaw_token" in mounts
    runtimes = ops_call(base, "runtimes.list") or {}
    acp_ready = any(r.get("kind") == "acp" and r.get("binary_on_path") and any(a.get("available") for a in r.get("accounts", [])) for r in runtimes.get("runtimes", []))
    workers = ops_call(base, "jobs.workers") or {}
    # Same rule as scripts/pm2-deerflow.sh: the voice overlay is added only
    # when these three weight files exist.
    voice_dir = Path(os.path.expanduser(read_env("DEERFLOW_VOICE_MODEL_DIR") or "~/.cache/nova/voice"))
    voice_ok = (voice_dir / "voices-v1.0.bin").is_file() and ((voice_dir / "kokoro-v1.0.onnx").is_file() or (voice_dir / "kokoro-v1.0.int8.onnx").is_file())
    checks.append(evaluate_config_prereqs(text, {"voice_weights": voice_ok, "jobs_worker": bool(workers.get("items")), "acp_mounts": acp_mounts, "acp_ready": acp_ready}))

    endpoints = {
        "gateway /health": http_status(f"{base}/health"),
        "nova-ops :4100": http_status("http://127.0.0.1:4100/"),
        "litellm :4000": http_status("http://172.17.0.1:4000/v1/models"),
        "llama-server :8086": http_status("http://127.0.0.1:8086/v1/models"),
        "public": http_status("https://nova.alilabsx.com/health", timeout=15),
    }
    # By compose label, not by name: a recreate interrupted mid-way leaves the
    # old container renamed (`<id>_deer-flow-frontend`) but still serving.
    _, cid = run(["docker", "ps", "-q", "--filter", "label=com.docker.compose.project=deer-flow-dev", "--filter", "label=com.docker.compose.service=frontend"], timeout=30)
    cid = cid.strip().splitlines()[0] if cid.strip() else "deer-flow-frontend"
    _, served = run(["docker", "exec", cid, "cat", "/app/frontend/.next/BUILD_ID"], timeout=30)
    if "Error" in served or "No such" in served:
        served = ""
    _, image_build = run(["docker", "run", "--rm", "--entrypoint", "cat", "deer-flow-dev-frontend:latest", "/app/frontend/.next/BUILD_ID"], timeout=60)
    checks.append(evaluate_services(pm2_status(), endpoints, served.strip() or None, image_build.strip() or None))

    checks.append(evaluate_e2e_toolchain(playwright_expected_revision(), Path.home() / ".cache" / "ms-playwright"))

    skills = ops_call(base, "skills.list") or {}
    mcp = ops_call(base, "mcp.servers") or {}
    caps = None
    try:
        req = urllib.request.Request(f"{base}/api/capabilities/ops", headers={"X-Nova-Ops-Token": read_env("NOVA_OPS_TOKEN") or ""})
        with urllib.request.build_opener(urllib.request.ProxyHandler({})).open(req, timeout=20) as resp:  # noqa: S310
            caps = json.loads(resp.read().decode("utf-8"))
    except (urllib.error.URLError, OSError, ValueError):
        caps = None
    config_tools = len(re.findall(r"^- name: [a-z_]+\n  group:", text, re.MULTILINE))
    # Use manifest tool count (most accurate) or fall back to groups minus missing
    sandbox_tool_count = len(manifest_tools) if manifest_tools else (
        sum(len(v) for v in SANDBOX_TOOLCHAIN.values()) - sum(len(v) for v in missing.values()) if exists else 0
    )
    counts = {
        "skills": int(skills.get("total", 0)),
        "config_tools": config_tools,
        "capability_ops": len((caps or {}).get("snapshot", {}).get("operations", [])),
        "capability_modules": len((caps or {}).get("snapshot", {}).get("modules", [])),
        "mcp_servers": int(mcp.get("total", 0)),
        "sandbox_tools": sandbox_tool_count,
        "overlays": len(running_overlays()),
    }
    baseline = json.loads(BASELINE.read_text(encoding="utf-8")) if BASELINE.is_file() else None
    checks.append(evaluate_counts(counts, baseline))
    return checks, counts


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--base", default=os.environ.get("NOVA_GATEWAY_BASE", "http://127.0.0.1:2026"))
    parser.add_argument("--out", default=os.environ.get("NOVA_GATES_DIR", str(Path.home() / ".nova" / "gates")))
    parser.add_argument("--json", action="store_true")
    parser.add_argument("--write-baseline", action="store_true", help="pin today's inventory counts as the floor")
    args = parser.parse_args(argv)

    checks, counts = collect(args.base)
    if args.write_baseline:
        BASELINE.write_text(json.dumps(counts, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        print(f"baseline written: {BASELINE}")
    report = build_report(checks)
    write_atomic(Path(args.out) / "regression.json", report)
    if args.json:
        print(json.dumps(report, indent=2))
    else:
        print(f"regression gate: {report['overall'].upper()}")
        for c in checks:
            print(f"  [{c['status'].upper():6}] {c['name']:18} {c['detail'][:160]}")
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    sys.exit(main())
