"""Per-tool-call preparation of the sandbox Chromium over CDP.

``_cdp_browser_op`` (workspace_tools) attaches to the sandbox's browser for one
operation. When it is handed a :class:`LayerContext`, it calls
:func:`prepared` around the operation, which in order:

1. on the thread's first attach, loads its profile — the one picked with
   ``browser_profile_load``, else the user's default (``profiles.auto_load_default``)
   — as cookies (persist in the browser) plus a localStorage seeding script;
2. applies stealth / locale / timezone / viewport / blocking as CDP commands on
   the page session (see ``engines.cdp_page_commands``);
3. takes over download behaviour so downloads land in outputs/downloads;
4. starts the recorder when recording is on for the thread;
5. after the op, probes for a captcha / challenge and records the action.

CDP page overrides are session-scoped: they hold for everything the op does
(the navigation itself, the click and what it triggers) and lapse when Nova
detaches, which is also why the user's own interaction in the live view is
never altered behind their back. Cookies and localStorage, by contrast, are
written into the browser and persist.
"""

from __future__ import annotations

import contextlib
import json
import logging
from collections.abc import Iterator
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from nova.browser import thread_state
from nova.browser.challenge import BlockSignal, probe_page
from nova.browser.downloads import DOWNLOADS_SUBDIR, VIRTUAL_OUTPUTS, DownloadWatcher
from nova.browser.engines import cdp_page_commands
from nova.config.browser_config import BrowserConfig, get_browser_config

logger = logging.getLogger(__name__)

_PAGE_TOOLS_THAT_PROBE = {"browser_navigate", "browser_click", "browser_input"}


@dataclass
class LayerContext:
    tool: str
    thread_id: str | None = None
    user_id: str | None = None
    outputs_dir: Path | None = None  # host path of /mnt/user-data/outputs
    browser_outputs_dir: str = VIRTUAL_OUTPUTS  # the same directory as the browser sees it
    detail: dict[str, Any] = field(default_factory=dict)
    capture_downloads: bool = False
    config: BrowserConfig | None = None
    # filled in by prepared()
    downloads: list[dict[str, Any]] = field(default_factory=list)
    block: BlockSignal | None = None
    profile_loaded: str | None = None
    watcher: DownloadWatcher | None = None

    @property
    def cfg(self) -> BrowserConfig:
        if self.config is None:
            self.config = get_browser_config()
        return self.config

    @property
    def thread_dir(self) -> Path | None:
        return thread_state.thread_dir_from_outputs(self.outputs_dir)

    def recording_on(self) -> bool:
        if self.outputs_dir is None:
            return False
        flag = thread_state.read_settings(self.thread_dir).get("recording")
        return bool(flag) if flag is not None else self.cfg.recording.enabled

    def pending_profile(self) -> str | None:
        """Profile to load on this attach, if any."""
        if not (self.thread_id and self.user_id and self.cfg.profiles.enabled):
            return None
        if not thread_state.first_attach(self.thread_id):
            return None
        chosen = thread_state.read_settings(self.thread_dir).get("profile")
        if chosen:
            return str(chosen)
        if self.cfg.profiles.auto_load_default:
            try:
                from nova.browser.profiles import ProfileStore

                return ProfileStore(self.user_id, config=self.cfg.profiles).default_name()
            except Exception:  # noqa: BLE001
                return None
        return None

    def wants_cdp(self) -> bool:
        """True when the SDK REST path cannot honour what this call needs."""
        c = self.cfg
        if c.stealth.enabled or c.stealth.user_agent or c.stealth.locale or c.timezone or c.viewport.set or c.blocking.enabled:
            return True
        if self.capture_downloads and c.downloads.enabled:
            return True
        if self.recording_on():
            return True
        return self.pending_profile() is not None


def localstorage_seed_script(origins: list[dict[str, Any]]) -> str:
    """Init script that writes a profile's localStorage for matching origins (never overwrites)."""
    table = {o["origin"]: [[i.get("name"), i.get("value")] for i in o.get("localStorage") or []] for o in origins if o.get("origin")}
    return "(() => { try { const t = " + json.dumps(table) + "; const items = t[location.origin]; if (!items) return; for (const [k, v] of items) { if (localStorage.getItem(k) === null) localStorage.setItem(k, v); } } catch (e) {} })();"


def apply_profile(context, page, user_id: str, name: str, cfg: BrowserConfig) -> dict[str, Any]:
    """Inject a stored profile into the live browser context. Returns counts (no values)."""
    from nova.browser.profiles import ProfileStore

    prof = ProfileStore(user_id, config=cfg.profiles).load(name)
    cookies = [_playwright_cookie(c) for c in prof.state.get("cookies") or []]
    cookies = [c for c in cookies if c]
    if cookies:
        context.add_cookies(cookies)
    origins = prof.state.get("origins") or []
    if origins:
        script = localstorage_seed_script(origins)
        with contextlib.suppress(Exception):
            page.add_init_script(script)
        with contextlib.suppress(Exception):
            page.evaluate(script)  # the page already open, if it is one of them
    return {"name": name, "cookies": len(cookies), "origins": len(origins), "domains": prof.domains}


def _playwright_cookie(c: dict[str, Any]) -> dict[str, Any] | None:
    if not c.get("name") or not c.get("domain"):
        return None
    out = {"name": c["name"], "value": str(c.get("value", "")), "domain": c["domain"], "path": c.get("path") or "/"}
    if isinstance(c.get("expires"), (int, float)) and c["expires"] > 0:
        out["expires"] = float(c["expires"])
    for k in ("httpOnly", "secure"):
        if k in c:
            out[k] = bool(c[k])
    if c.get("sameSite") in ("Strict", "Lax", "None"):
        out["sameSite"] = c["sameSite"]
    return out


def _apply_page_settings(context, page, cfg: BrowserConfig) -> None:
    current_ua = ""
    with contextlib.suppress(Exception):
        current_ua = page.evaluate("navigator.userAgent")
    cmds = cdp_page_commands(cfg, current_ua)
    if not cmds:
        return
    try:
        cdp = context.new_cdp_session(page)
    except Exception:  # noqa: BLE001
        return
    for method, params in cmds:
        try:
            cdp.send(method, params)
        except Exception as exc:  # noqa: BLE001 - e.g. timezone already overridden
            logger.debug("CDP %s failed: %s", method, exc)
    # The session must outlive the op for its overrides to hold; Playwright
    # detaches it when the browser connection closes.


@contextlib.contextmanager
def prepared(browser, context, page, lc: LayerContext) -> Iterator[LayerContext]:
    cfg = lc.cfg
    name = lc.pending_profile()
    if name and lc.thread_id:
        try:
            info = apply_profile(context, page, lc.user_id or "", name, cfg)
            lc.profile_loaded = name
            logger.info("Loaded browser profile %r into thread %s (%d cookies, %d origins)", name, lc.thread_id, info["cookies"], info["origins"])
        except Exception as exc:  # noqa: BLE001 - a bad profile must not block browsing
            logger.warning("Could not load browser profile %r: %s", name, exc)
        thread_state.mark_attached(lc.thread_id, name)
    elif lc.thread_id and thread_state.first_attach(lc.thread_id):
        thread_state.mark_attached(lc.thread_id, None)

    _apply_page_settings(context, page, cfg)

    if lc.capture_downloads and cfg.downloads.enabled and lc.outputs_dir is not None:
        with contextlib.suppress(OSError):
            (Path(lc.outputs_dir) / DOWNLOADS_SUBDIR).mkdir(parents=True, exist_ok=True)
        watcher = DownloadWatcher(browser_dir=f"{lc.browser_outputs_dir.rstrip('/')}/{DOWNLOADS_SUBDIR}")
        if watcher.attach(browser):
            lc.watcher = watcher

    recorder = None
    if lc.recording_on() and lc.outputs_dir is not None:
        try:
            from nova.browser.recording import Recorder

            recorder = Recorder(Path(lc.outputs_dir), cfg.recording)
            recorder.start(page)
        except Exception:  # noqa: BLE001
            logger.debug("recorder failed to start", exc_info=True)
            recorder = None

    error: str | None = None
    try:
        yield lc
    except Exception as exc:
        error = str(exc)
        raise
    finally:
        if lc.watcher is not None:
            with contextlib.suppress(Exception):
                lc.downloads = lc.watcher.wait(page, finish_s=cfg.downloads.wait_s)
            lc.watcher.detach()
        if error is None and cfg.captcha_detection and lc.tool in _PAGE_TOOLS_THAT_PROBE:
            lc.block = probe_page(page)
            if lc.block is not None and lc.block.needs_human:
                title = ""
                with contextlib.suppress(Exception):
                    title = page.title()
                thread_state.raise_attention(lc.thread_dir, kind=lc.block.kind, url=page.url, title=title, reason=lc.block.reason)
        if recorder is not None:
            with contextlib.suppress(Exception):
                recorder.finish(page, lc.tool, lc.detail, ok=error is None, error=error)


def needs_human_message(block: BlockSignal, url: str, profile_hint: bool = True) -> str:
    """The structured result a tool returns when a human has to act."""
    payload = {
        "status": "needs_human",
        "kind": block.kind,
        "url": url,
        "reason": block.reason,
        "next": "Ask the user to solve it in the Browser tab's live view (the panel shows a prompt), then call browser_await_human. Do not try to solve captchas yourself.",
    }
    if profile_hint:
        payload["after"] = "Once through, offer to keep the login with browser_profile_save so this is not needed again."
    return "NEEDS_HUMAN " + json.dumps(payload)
