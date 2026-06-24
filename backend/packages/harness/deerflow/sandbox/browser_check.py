"""Agent browser self-test using the AIO sandbox's native headless browser.

The per-thread AIO container already ships a real Chromium reachable through the
``agent_sandbox`` SDK (``browser_page``: navigate / get_console / get_html /
screenshot). We drive it against the thread's *running dev server* (in-container
``localhost:{port}``) to answer "does the app actually load and run?" — capturing
console errors, HTTP/render failures, and a screenshot.

This is the signal source the self-improving loop (verify gate) consumes, and the
Browser tab surfaces the screenshot so the user can watch the agent test live.
Zero install: it reuses the sandbox's bundled browser.
"""

from __future__ import annotations

import base64
import contextlib
import logging
import re
import shlex
from dataclasses import dataclass, field
from typing import Any

from deerflow.sandbox.dev_server import get_dev_server

logger = logging.getLogger(__name__)

# Server-rendered failure fingerprints (no JS runtime needed to catch these).
_HTML_ERROR_MARKERS = [
    "Cannot find module",
    "Module not found",
    "Unhandled Runtime Error",
    "Application error: a client-side exception",
    "__next_error__",
    "vite-error-overlay",
    "Internal Server Error",
    "ECONNREFUSED",
    "This site can’t be reached",
    "This site can't be reached",
]


@dataclass
class RouteResult:
    route: str
    ok: bool
    status: str  # ok | console_errors | render_error | unreachable
    console_errors: list[str] = field(default_factory=list)
    notes: str = ""
    screenshot_b64: str | None = None  # populated only when with_screenshot

    def to_dict(self, *, include_screenshot: bool = True) -> dict:
        d = {
            "route": self.route,
            "ok": self.ok,
            "status": self.status,
            "console_errors": self.console_errors[:20],
            "notes": self.notes,
        }
        if include_screenshot:
            d["screenshot"] = (f"data:image/png;base64,{self.screenshot_b64}" if self.screenshot_b64 else None)
        return d


@dataclass
class BrowserCheck:
    ok: bool = False
    reason: str = ""
    port: int | None = None  # the in-container port actually tested
    routes: list[RouteResult] = field(default_factory=list)

    def to_dict(self, *, include_screenshot: bool = True) -> dict:
        return {
            "ok": self.ok,
            "reason": self.reason,
            "port": self.port,
            "routes": [r.to_dict(include_screenshot=include_screenshot) for r in self.routes],
        }

    def summary(self) -> str:
        if not self.routes:
            return f"Browser check could not run: {self.reason}"
        lines = [f"Browser self-test: {'✓ PASS' if self.ok else '✗ ISSUES'}"]
        for r in self.routes:
            mark = "✓" if r.ok else "✗"
            lines.append(f"  {mark} {r.route} [{r.status}]" + (f" — {r.notes}" if r.notes else ""))
            for ce in r.console_errors[:5]:
                lines.append(f"      console: {ce}")
        return "\n".join(lines)


def _console_entries_to_errors(entries: Any) -> list[str]:
    """Extract error/warning console lines from a get_console() response."""
    out: list[str] = []
    items = getattr(entries, "data", None)
    if items is None:
        items = entries if isinstance(entries, list) else []
    for it in items or []:
        if isinstance(it, dict):
            level = str(it.get("type") or it.get("level") or "").lower()
            text = str(it.get("text") or it.get("message") or it)
        else:
            level = str(getattr(it, "type", None) or getattr(it, "level", "")).lower()
            text = str(getattr(it, "text", None) or getattr(it, "message", None) or it)
        if level in ("error", "warning") or "error" in level:
            out.append(text[:300])
    return out


# Ports the sandbox itself listens on — never treat these as the app.
_SANDBOX_PORTS = {8080, 8079, 9222, 5900, 6080, 8088}
# Common dev-server ports, preferred when several app ports are listening.
_COMMON_DEV_PORTS = [3000, 5173, 4321, 8000, 8080, 4000, 3001, 5000, 8888, 4100, 4101, 4102]


def _detect_app_port(sandbox: Any, *, prefer: int) -> int:
    """Find the port the app actually listens on inside the container.

    Prefers ``prefer`` (the registry's assigned port) when it is actually
    listening; otherwise picks the most likely app port from `ss`/`netstat`,
    excluding the sandbox's own service ports. Falls back to ``prefer``.
    """
    try:
        out = sandbox.execute_command(
            "ss -ltn 2>/dev/null || netstat -ltn 2>/dev/null"
        ) or ""
    except Exception:
        return prefer

    listening: set[int] = set()
    for line in out.splitlines():
        # Match the local-address column's :PORT (IPv4/IPv6/wildcard).
        for m in re.finditer(r"[\d.*\]]:(\d{2,5})\b", line):
            try:
                listening.add(int(m.group(1)))
            except ValueError:
                pass
    candidates = {p for p in listening if p not in _SANDBOX_PORTS and 1024 <= p <= 65535}
    if prefer in candidates:
        return prefer
    for p in _COMMON_DEV_PORTS:
        if p in candidates:
            return p
    if candidates:
        return min(candidates)
    return prefer


def _scan_html_errors(html: str) -> str | None:
    """Explicit error fingerprints only. Do NOT infer 'empty body' from the DOM —
    canvas / JS-rendered apps legitimately have empty innerText; blank-ness is judged
    from the actual screenshot pixels instead (see the CDP engine)."""
    for marker in _HTML_ERROR_MARKERS:
        if marker in html:
            return marker
    return None


# A real blank page screenshots to a tiny PNG; rendered content (even a canvas)
# produces a larger image. Used to detect "blank" by pixels, not by innerText.
_BLANK_SCREENSHOT_MAX_BYTES = 2000


# Latest check per thread so the panel can auto-show the most recent self-test
# (e.g. the deterministic auto-check the preview pipeline runs) without re-running.
_last_checks: dict[str, BrowserCheck] = {}


def get_last_browser_check(thread_id: str) -> BrowserCheck | None:
    return _last_checks.get(thread_id)


def run_browser_check(
    thread_id: str,
    sandbox: Any,
    *,
    label: str = "app",
    routes: list[str] | None = None,
    with_screenshot: bool = True,
) -> BrowserCheck:
    """Drive the sandbox browser against the running dev server — or, when there
    is none, against the self-contained HTML deliverable shipped via present_files
    (opened with file://), so static builds are still observable."""
    result = BrowserCheck()
    client = getattr(sandbox, "_client", None)
    if client is None:
        result.reason = "this sandbox has no browser (container/AIO sandbox required)"
        return result

    handle = get_dev_server(thread_id, label)
    # targets: list of (label, kind, value) where kind is "url" (goto) or "html" (set_content).
    targets: list[tuple[str, str, str]] = []
    if handle is not None and handle.status in ("starting", "ready"):
        # The browser runs INSIDE the container, so it can reach any listening port,
        # not just the published one. Detect the real listening port so the self-test
        # is accurate ("direct port is the source of truth").
        assigned = getattr(handle, "container_port", None) or handle.port
        port = _detect_app_port(sandbox, prefer=assigned)
        result.port = port
        base = f"http://localhost:{port}"
        for r in (routes or ["/"]):
            r = r if r.startswith("/") else "/" + r
            targets.append((r, "url", f"{base}{r}"))
    else:
        # No dev server — verify the latest present_files HTML deliverable by reading
        # its content and rendering it via set_content (file:// is blocked in this
        # sandbox's browser; set_content needs no file/port access).
        static_path = ""
        try:
            out = sandbox.execute_command("ls -t /mnt/user-data/outputs/*.html 2>/dev/null | head -1") or ""
            static_path = next((ln.strip() for ln in out.splitlines() if ln.strip().endswith(".html")), "")
        except Exception:
            static_path = ""
        if not static_path:
            result.reason = "no dev server running and no HTML deliverable in /mnt/user-data/outputs/"
            return result
        html = ""
        try:
            html = sandbox.execute_command(f"cat {shlex.quote(static_path)}") or ""
        except Exception:
            html = ""
        if not (html.lstrip()[:400].lower().startswith("<!") or "<html" in html[:400].lower()):
            result.reason = f"could not read deliverable {static_path.split('/')[-1]}"
            return result
        targets.append((static_path.split("/")[-1], "html", html))

    # Primary engine: Playwright over the existing AIO chromium's CDP endpoint. The
    # SDK's browser_page HTTP API is 404 on this sandbox image, but CDP works — so we
    # drive a real Playwright page (goto for servers, set_content for static HTML).
    cdp = _cdp_url_for_gateway(client)
    routes_result: list[RouteResult] | None = None
    if cdp:
        try:
            routes_result = _run_targets_via_cdp(cdp, targets, with_screenshot)
        except Exception as e:
            logger.warning("browser_check CDP engine failed (%s); falling back to browser_page", e)
            routes_result = None
    if routes_result is None:
        routes_result = _run_targets_via_browser_page(getattr(client, "browser_page", None), targets, with_screenshot)

    result.routes = routes_result
    result.ok = bool(routes_result) and all(r.ok for r in routes_result)
    result.reason = "ok" if result.ok else "issues found — see routes"
    _last_checks[thread_id] = result
    return result

def _cdp_url_for_gateway(client: Any) -> str | None:
    """The AIO chromium's CDP websocket, rewritten to be reachable from the gateway."""
    try:
        info = client.browser.get_info()
        cdp = getattr(getattr(info, "data", info), "cdp_url", None)
    except Exception:
        return None
    if not cdp:
        return None
    return cdp.replace("localhost:8080", "host.docker.internal:8080").replace("127.0.0.1:8080", "host.docker.internal:8080")


def _run_targets_via_cdp(cdp_url: str, targets: list[tuple[str, str, str]], with_screenshot: bool) -> list[RouteResult]:
    """Drive the existing browser over CDP with Playwright. Raises if it can't connect."""
    from playwright.sync_api import sync_playwright

    out: list[RouteResult] = []
    with sync_playwright() as pw:
        browser = pw.chromium.connect_over_cdp(cdp_url, timeout=15000)
        ctx = browser.contexts[0] if browser.contexts else browser.new_context()
        for name, kind, value in targets:
            rr = RouteResult(route=name, ok=True, status="ok")
            errors: list[str] = []
            page = ctx.new_page()
            page.on("console", lambda m: errors.append(f"{m.type}: {m.text}"[:300]) if m.type == "error" else None)
            try:
                if kind == "url":
                    page.goto(value, wait_until="load", timeout=20000)
                else:
                    page.set_content(value, wait_until="load", timeout=20000)
                # Give JS a beat to render (canvas/DOMContentLoaded apps populate after load).
                page.wait_for_timeout(1500)

                # Capture the screenshot first — it's the ground truth for "did it render".
                shot = b""
                try:
                    shot = page.screenshot(full_page=False)
                    rr.screenshot_b64 = base64.b64encode(shot).decode("ascii")
                except Exception:
                    pass

                # Explicit error fingerprints (real failures, not heuristics).
                marker = _scan_html_errors(page.content())
                if marker:
                    rr.ok, rr.status, rr.notes = False, "render_error", marker

                # Blank detection by PIXELS, not innerText — a canvas/visual app has
                # empty innerText but a non-trivial screenshot, so it's NOT blank.
                if rr.ok:
                    try:
                        body_text = page.inner_text("body").strip()
                    except Exception:
                        body_text = ""
                    if not body_text and (not shot or len(shot) < _BLANK_SCREENSHOT_MAX_BYTES):
                        rr.ok, rr.status, rr.notes = False, "blank", "page rendered blank (no text, no pixels)"

                rr.console_errors = errors
                if errors and rr.ok:
                    rr.ok, rr.status = False, "console_errors"
            except Exception as e:
                rr.ok, rr.status, rr.notes = False, "unreachable", str(e)[:160]
            finally:
                with contextlib.suppress(Exception):
                    page.close()
            out.append(rr)
    return out


def _run_targets_via_browser_page(page: Any, targets: list[tuple[str, str, str]], with_screenshot: bool) -> list[RouteResult]:
    """Fallback using the SDK browser_page HTTP API (URL targets only; may 404 on
    older sandbox images — graceful degradation when CDP is unavailable)."""
    out: list[RouteResult] = []
    for name, kind, value in targets:
        rr = RouteResult(route=name, ok=True, status="ok")
        if page is None or kind != "url":
            rr.ok, rr.status, rr.notes = False, "unreachable", "browser navigation unavailable in this sandbox"
            out.append(rr)
            continue
        try:
            page.navigate(url=value, wait_until="load", timeout=20000)
        except Exception as e:
            rr.notes = f"navigate: {str(e)[:160]}"
        try:
            rr.console_errors = _console_entries_to_errors(page.get_console())
        except Exception:
            pass
        try:
            html_resp = page.get_html()
            html = getattr(html_resp, "data", None) or (html_resp if isinstance(html_resp, str) else "")
            marker = _scan_html_errors(str(html))
            if marker:
                rr.ok, rr.status = False, "render_error"
                rr.notes = (rr.notes + " | " if rr.notes else "") + marker
        except Exception as e:
            rr.ok, rr.status = False, "unreachable"
            rr.notes = (rr.notes + " | " if rr.notes else "") + f"get_html: {str(e)[:120]}"
        if rr.console_errors and rr.ok:
            rr.ok, rr.status = False, "console_errors"
        if with_screenshot:
            try:
                chunks = b"".join(page.screenshot(full_page=False))
                if chunks:
                    rr.screenshot_b64 = base64.b64encode(chunks).decode("ascii")
            except Exception:
                pass
        out.append(rr)
    return out
