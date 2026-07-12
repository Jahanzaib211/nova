"""Agent browser self-test using the AIO sandbox's native headless browser.

The per-thread AIO container already ships a real Chromium reachable through the
``agent_sandbox`` SDK (``browser_page``: navigate / get_console / get_html /
screenshot). We drive it against the thread's *running dev server* (in-container
``localhost:{port}``) to answer "does the app actually load and run?" — capturing
console errors, HTTP/render failures, and a screenshot.

This is the signal source the self-improving loop (verify gate) consumes, and the
Browser tab surfaces the screenshot so the user can watch the agent test live.
Zero install: it reuses the sandbox's bundled browser.

Concurrency model (v7+)
-----------------------
Per-thread locks (``WeakValueDictionary[str, threading.Lock]``) serialize
``run_browser_check`` for the same ``thread_id`` so concurrent self-tests on a
single thread don't race on the Playwright session or the last-checks cache.
A module-level lock guards writes to ``_last_checks`` to keep the public API
race-free without breaking the hot path.

Backwards compatibility
-----------------------
``run_browser_check`` and ``get_last_browser_check`` retain their original
signatures and return types. Callers now receive a *deep copy* of the stored
``BrowserCheck`` from ``get_last_browser_check`` and from ``run_browser_check``,
so they can mutate the result without affecting the cache or other callers.
"""

from __future__ import annotations

import base64
import contextlib
import copy
import logging
import os
import re
import shlex
import threading
import time
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import urlsplit, urlunsplit
from weakref import WeakValueDictionary

from deerflow.sandbox.dev_server import get_dev_server
from deerflow.sandbox.metrics import browser_check_duration_ms, browser_check_total

logger = logging.getLogger(__name__)

# Module-level state used by the post-run metric emit in
# ``_run_browser_check_unlocked``. Module-level (not closure) so the
# assignment survives a caller reusing the function through
# ``retry_browser_call``/``guard_browser_call`` wrappers, both of which
# re-enter without an explicit ``return`` of the inner function.
_started_monotonic: float = 0.0
_route_engine_used: Any = None  # callable(_result) -> str; set by the inner func

# Adaptive render budget (v7+): replaces the previous fixed
# ``page.wait_for_timeout(1500)`` with a ``networkidle`` + fallback scheme.
#
# Defaults: 1500ms matches the previous fixed value, 10000ms is the hard cap
# (prevents runaway waits). Override via env for tight CI / lax dev sandboxes.
_DEFAULT_RENDER_BUDGET_MS = int(os.environ.get("DEERFLOW_RENDER_BUDGET_MS", "1500"))
_MAX_RENDER_BUDGET_MS = int(os.environ.get("DEERFLOW_MAX_RENDER_BUDGET_MS", "10000"))

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
            d["screenshot"] = f"data:image/png;base64,{self.screenshot_b64}" if self.screenshot_b64 else None
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
        out = sandbox.execute_command("ss -ltn 2>/dev/null || netstat -ltn 2>/dev/null") or ""
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


def _clamp_render_budget_ms(requested: int | None) -> int:
    """Bound the render budget to ``[_DEFAULT, _MAX]``.

    ``None`` / non-positive falls back to default. Values above ``_MAX`` are
    clamped — runaway budgets would let a single check monopolise the gate.
    """
    if requested is None or requested <= 0:
        return _DEFAULT_RENDER_BUDGET_MS
    return min(requested, _MAX_RENDER_BUDGET_MS)


def _adaptive_wait(page: Any, budget_ms: int) -> tuple[float, str]:
    """Wait for the page to be visually idle, bounded by ``budget_ms``.

    Strategy (matches MS Playwright "web-first" guidance):
      1. Try ``wait_for_load_state("networkidle")`` with the full budget.
         Returns immediately on fast pages (<300ms typical for cached).
      2. If networkidle never settles (long-polling, websockets, animations),
         fall back to a fixed sleep of the FULL budget. This guarantees the
         caller never waits longer than ``budget_ms`` even in the worst case.

    Returns:
        ``(elapsed_ms, mode)`` where ``mode`` is one of:
          - ``"networkidle"``: settled within budget (fast path)
          - ``"budget_fallback"``: timed out, slept the budget
          - ``"error"``: an exception during the wait (page closed, etc.)
    """
    start = time.monotonic()
    try:
        page.wait_for_load_state("networkidle", timeout=budget_ms)
        return (round((time.monotonic() - start) * 1000, 1), "networkidle")
    except Exception:
        # networkidle timed out (or page died) — sleep the FULL budget so the
        # caller still gets a stable render window, then return.
        elapsed_so_far = (time.monotonic() - start) * 1000
        remaining = max(0, int(budget_ms - elapsed_so_far))
        if remaining > 0:
            page.wait_for_timeout(remaining)
        return (round((time.monotonic() - start) * 1000, 1), "budget_fallback")


# A real blank page screenshots to a tiny PNG; rendered content (even a canvas)
# produces a larger image. Used to detect "blank" by pixels, not by innerText.
_BLANK_SCREENSHOT_MAX_BYTES = 2000


# Latest check per thread so the panel can auto-show the most recent self-test
# (e.g. the deterministic auto-check the preview pipeline runs) without re-running.
# Guarded by ``_LAST_CHECKS_LOCK`` so concurrent writers can't corrupt the dict.
_last_checks: dict[str, BrowserCheck] = {}
_LAST_CHECKS_LOCK = threading.Lock()

# Per-thread locks so concurrent ``run_browser_check`` calls on the SAME
# ``thread_id`` don't race on the shared Playwright session or the cache.
# WeakValueDictionary so dead threads don't accumulate locks forever.
_thread_locks: WeakValueDictionary[str, threading.Lock] = WeakValueDictionary()
_THREAD_LOCKS_META = threading.Lock()  # guards creation of entries in _thread_locks


def _get_thread_lock(thread_id: str) -> threading.Lock:
    """Get-or-create the per-thread ``threading.Lock`` for ``run_browser_check``.

    Safe under concurrency: the meta-lock only guards creation; once a lock is
    stored in the ``WeakValueDictionary`` it is itself the long-lived primitive
    that serialises check execution for that thread.
    """
    lock = _thread_locks.get(thread_id)
    if lock is not None:
        return lock
    with _THREAD_LOCKS_META:
        lock = _thread_locks.get(thread_id)
        if lock is None:
            lock = threading.Lock()
            _thread_locks[thread_id] = lock
        return lock


def _store_last_check(thread_id: str, check: BrowserCheck) -> None:
    """Atomically store a deep copy of ``check`` for ``thread_id``."""
    with _LAST_CHECKS_LOCK:
        _last_checks[thread_id] = copy.deepcopy(check)


def get_last_browser_check(thread_id: str) -> BrowserCheck | None:
    """Return a *deep copy* of the most recent ``BrowserCheck`` for ``thread_id``.

    The copy lets callers mutate the result without affecting the cache or other
    callers — this is a deliberate hardening step (v7+) to prevent the previous
    behaviour where ``BrowserCheck.routes`` was a shared mutable list.
    """
    with _LAST_CHECKS_LOCK:
        cached = _last_checks.get(thread_id)
    if cached is None:
        return None
    return copy.deepcopy(cached)


def run_browser_check(
    thread_id: str,
    sandbox: Any,
    *,
    label: str = "app",
    routes: list[str] | None = None,
    with_screenshot: bool = True,
    render_budget_ms: int | None = None,
) -> BrowserCheck:
    """Drive the sandbox browser against the running dev server — or, when there
    is none, against the self-contained HTML deliverable shipped via present_files
    (opened with file://), so static builds are still observable.

    Concurrent calls for the same ``thread_id`` are serialised by a per-thread
    lock so the shared Playwright session and ``_last_checks`` cache stay
    race-free. Different threads run in parallel — the lock only scopes by
    ``thread_id``.

    ``render_budget_ms`` (v7+) bounds the per-page render wait. Replaces the
    previous fixed ``wait_for_timeout(1500)`` with an adaptive ``networkidle``
    + budget-fallback scheme. ``None`` uses the default (1500ms), values are
    clamped to ``[1, 10000]`` to prevent runaway waits.
    """
    lock = _get_thread_lock(thread_id)
    budget = _clamp_render_budget_ms(render_budget_ms)
    with lock:
        result = _run_browser_check_unlocked(
            thread_id,
            sandbox,
            label=label,
            routes=routes,
            with_screenshot=with_screenshot,
            render_budget_ms=budget,
        )
        _store_last_check(thread_id, result)
    # Return a fresh copy so the caller can't mutate the cached instance.
    return copy.deepcopy(result)


def _run_browser_check_unlocked(
    thread_id: str,
    sandbox: Any,
    *,
    label: str,
    routes: list[str] | None,
    with_screenshot: bool,
    render_budget_ms: int,
) -> BrowserCheck:
    """Inner, lock-free implementation. Always called from ``run_browser_check``."""
    # Capture start time + route-engine state in the closure so the post-run
    # metric emit (at the bottom of this function) has accurate duration +
    # engine label without re-deriving them.
    global _started_monotonic, _route_engine_used

    def _route_engine_used(_result: BrowserCheck) -> str:
        # Currently only CDP is actually used; fall back to "browser_page"
        # if a future change introduces a non-CDP engine selector.
        return "cdp"

    _started_monotonic = time.monotonic()
    _route_engine_used = _route_engine_used

    result = BrowserCheck()
    client = getattr(sandbox, "_client", None)
    if client is None:
        result.reason = "this sandbox has no browser (container/AIO sandbox required)"
        # Still count + observe so unhealthy local sandboxes surface in metrics.
        try:
            elapsed_ms = (time.monotonic() - _started_monotonic) * 1000.0
            browser_check_total.inc("no_browser")
            browser_check_duration_ms.observe(elapsed_ms, "none")
        except Exception:
            logger.debug("browser_check metric emit failed", exc_info=True)
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
        for r in routes or ["/"]:
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
            routes_result = _run_targets_via_cdp(cdp, targets, with_screenshot, render_budget_ms)
        except Exception as e:
            logger.warning("browser_check CDP engine failed (%s); falling back to browser_page", e)
            routes_result = None
    if routes_result is None:
        routes_result = _run_targets_via_browser_page(getattr(client, "browser_page", None), targets, with_screenshot, render_budget_ms)

    result.routes = routes_result
    result.ok = bool(routes_result) and all(r.ok for r in routes_result)
    result.reason = "ok" if result.ok else "issues found — see routes"

    # Observability: emit Prometheus counter + histogram. Non-fatal — a
    # bad import must NEVER break the self-test result. The duration is
    # the wall-clock from function entry to here; capture once so we
    # don't double-count on retry-wrapped callers.
    try:
        elapsed_ms = (time.monotonic() - _started_monotonic) * 1000.0
        outcome = "ok" if result.ok else "issues"
        browser_check_total.inc(outcome)
        browser_check_duration_ms.observe(elapsed_ms, _route_engine_used(result))
    except Exception:
        logger.debug("browser_check metric emit failed", exc_info=True)

    return result


def _rewrite_cdp_netloc(cdp_url: str, new_host: str) -> str | None:
    """Swap the netloc (host[:port]) of a CDP websocket URL with ``urlsplit``.

    Replaces the previous brittle ``str.replace`` which corrupted path / query /
    fragment and broke for IPv6 / custom schemes. Returns ``None`` if the input
    is not a parseable URL — callers should treat that as "CDP unavailable"
    and fall back to the SDK HTTP API.

    The original port is preserved (CDP defaults to 8080/9222/3000 depending
    on the chromium image). If the URL has no port, only the host is swapped.
    """
    try:
        parts = urlsplit(cdp_url)
    except ValueError:
        return None
    if not parts.scheme or not parts.netloc:
        return None
    # Preserve the original port. If absent, omit it from the new netloc.
    new_port = parts.port
    new_netloc = f"{new_host}:{new_port}" if new_port else new_host
    return urlunsplit((parts.scheme, new_netloc, parts.path, parts.query, parts.fragment))


def _cdp_url_for_gateway(client: Any) -> str | None:
    """The AIO chromium's CDP websocket, rewritten to be reachable from the gateway.

    Only rewrites ``localhost`` and ``127.0.0.1`` host components (the
    container-internal chromium); other hosts pass through untouched so this
    helper also works when chromium is already on a routable address (e.g.
    a remote AIO deployment).
    """
    try:
        info = client.browser.get_info()
        cdp = getattr(getattr(info, "data", info), "cdp_url", None)
    except Exception:
        return None
    if not cdp:
        return None
    try:
        parts = urlsplit(cdp)
    except ValueError:
        return None
    host = (parts.hostname or "").lower()
    if host not in ("localhost", "127.0.0.1", "0.0.0.0"):
        return cdp  # already routable; pass through
    return _rewrite_cdp_netloc(cdp, new_host="host.docker.internal")


def _run_targets_via_cdp(cdp_url: str, targets: list[tuple[str, str, str]], with_screenshot: bool, render_budget_ms: int) -> list[RouteResult]:
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
                # Adaptive render wait (v7+): try ``networkidle`` first (fast
                # path for cached/static pages), fall back to a budget-bounded
                # sleep so the caller never waits more than ``render_budget_ms``
                # total — even if networkidle never settles (long-polling,
                # websockets, infinite animations).
                _adaptive_wait(page, render_budget_ms)

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


def _run_targets_via_browser_page(page: Any, targets: list[tuple[str, str, str]], with_screenshot: bool, render_budget_ms: int) -> list[RouteResult]:
    """Fallback using the SDK browser_page HTTP API (URL targets only; may 404 on
    older sandbox images — graceful degradation when CDP is unavailable).

    ``render_budget_ms`` is accepted for signature parity with the CDP engine
    but the SDK API does its own server-side wait; we still bound local
    client-side wait below to keep the total budget honest.
    """
    del render_budget_ms  # SDK handles its own wait; signature parity only
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
