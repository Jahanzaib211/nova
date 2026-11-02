"""Map the one `browser:` config onto each engine's own API.

| setting   | sandbox Chromium (CDP)                  | Browserless v2 /content            | Crawl4AI /crawl (untrusted REST) |
|-----------|-----------------------------------------|------------------------------------|----------------------------------|
| stealth   | init script + Emulation.setUserAgent…   | launch.stealth=true                | browser_config.enable_stealth    |
| UA        | Emulation.setUserAgentOverride          | body.userAgent.userAgent           | browser_config.user_agent        |
| locale    | Emulation.setLocaleOverride + Accept-L. | launch --lang + Accept-Language    | crawler_config.locale            |
| timezone  | Emulation.setTimezoneOverride           | not exposed by v2 /content         | crawler_config.timezone_id       |
| viewport  | Emulation.setDeviceMetricsOverride      | body.viewport                      | browser_config.viewport_*        |
| blocking  | Network.setBlockedURLs                  | rejectRequestPattern (+ blockAds)  | browser_config.avoid_ads         |
| proxy     | not settable over CDP (launch flag)     | launch --proxy-server + authenticate | forbidden per request (server env) |
| cookies   | Network/Storage via context.add_cookies | body.cookies                       | forbidden → routed to Browserless |

Crawl4AI's REST trust boundary 400s any request carrying `proxy`, `cookies`,
`headers`, `storage_state` or `init_scripts` on BrowserConfig, so those are
never sent — `crawl4ai_configs` only emits allowlisted fields, and
`tests/test_browser_engines.py` pins that.
"""

from __future__ import annotations

import json
import re
from typing import Any

from nova.browser.blocklist import blocked_hosts, cdp_url_patterns, regex_patterns
from nova.config.browser_config import BrowserConfig

# Fields the Crawl4AI server forbids from an untrusted request (400 on presence).
CRAWL4AI_FORBIDDEN_BROWSER_FIELDS = frozenset(
    {"proxy", "proxy_config", "extra_args", "user_data_dir", "channel", "chrome_channel", "cdp_url", "debugging_port", "host", "storage_state", "cookies", "headers", "init_scripts", "browser_context_id", "target_id"}
)
CRAWL4AI_FORBIDDEN_RUN_FIELDS = frozenset({"js_code", "js_code_before_wait", "c4a_script", "deep_crawl_strategy", "proxy_config", "proxy_rotation_strategy", "proxy_session_id", "simulate_user", "override_navigator", "magic", "session_id"})


def accept_language(locale: str) -> str:
    if not locale:
        return ""
    base = locale.split("-")[0]
    return f"{locale},{base};q=0.9" if base != locale else locale


def _blocking_hosts(cfg: BrowserConfig) -> list[str]:
    if not cfg.blocking.enabled:
        return []
    return blocked_hosts(use_bundled=cfg.blocking.use_bundled_list, extra=cfg.blocking.extra_hosts)


# ── Browserless ──────────────────────────────────────────────────────────────


def browserless_request(cfg: BrowserConfig, *, stealth: bool | None = None, cookies: list[dict[str, Any]] | None = None, proxy_server: str = "") -> tuple[dict[str, str], dict[str, Any]]:
    """(query params, body additions) for POST /content.

    ``stealth`` overrides ``cfg.stealth.enabled`` — the escalation ladder turns it
    on for its second rung even when the operator left it off.
    """
    query: dict[str, str] = {}
    body: dict[str, Any] = {}
    launch: dict[str, Any] = {}
    args: list[str] = []
    use_stealth = cfg.stealth.enabled if stealth is None else stealth
    if use_stealth:
        launch["stealth"] = True
    if cfg.stealth.user_agent:
        body["userAgent"] = {"userAgent": cfg.stealth.user_agent}
    headers: dict[str, str] = {}
    if cfg.stealth.locale:
        args.append(f"--lang={cfg.stealth.locale}")
        headers["Accept-Language"] = accept_language(cfg.stealth.locale)
    if headers:
        body["setExtraHTTPHeaders"] = headers
    if cfg.viewport.set:
        body["viewport"] = {"width": cfg.viewport.width, "height": cfg.viewport.height}
    hosts = _blocking_hosts(cfg)
    if hosts:
        body["rejectRequestPattern"] = regex_patterns(hosts)
    server = proxy_server or cfg.proxy.server
    if server.strip():
        args.append(f"--proxy-server={server.strip()}")
        if cfg.proxy.bypass:
            args.append(f"--proxy-bypass-list={cfg.proxy.bypass.replace(',', ';')}")
        if cfg.proxy.username:
            body["authenticate"] = {"username": cfg.proxy.username, "password": cfg.proxy.password()}
    if args:
        launch["args"] = args
    if launch:
        query["launch"] = json.dumps(launch, separators=(",", ":"))
    if cookies:
        body["cookies"] = [_browserless_cookie(c) for c in cookies]
    return query, body


def _browserless_cookie(c: dict[str, Any]) -> dict[str, Any]:
    out: dict[str, Any] = {"name": c["name"], "value": c.get("value", ""), "domain": c.get("domain"), "path": c.get("path") or "/"}
    for key in ("secure", "httpOnly"):
        if key in c:
            out[key] = bool(c[key])
    if c.get("sameSite") in ("Lax", "Strict", "None"):
        out["sameSite"] = c["sameSite"]
    if isinstance(c.get("expires"), (int, float)) and c["expires"] > 0:
        out["expires"] = c["expires"]
    return out


# ── Crawl4AI ─────────────────────────────────────────────────────────────────


def crawl4ai_configs(cfg: BrowserConfig) -> dict[str, Any]:
    """`browser_config` / `crawler_config` additions for POST /crawl — allowlisted fields only."""
    bparams: dict[str, Any] = {}
    rparams: dict[str, Any] = {}
    if cfg.stealth.enabled:
        bparams["enable_stealth"] = True
    if cfg.stealth.user_agent:
        bparams["user_agent"] = cfg.stealth.user_agent
    if cfg.viewport.set:
        bparams["viewport_width"] = cfg.viewport.width
        bparams["viewport_height"] = cfg.viewport.height
    if cfg.blocking.enabled:
        bparams["avoid_ads"] = True
    if cfg.stealth.locale:
        rparams["locale"] = cfg.stealth.locale
    if cfg.timezone:
        rparams["timezone_id"] = cfg.timezone
    assert not (set(bparams) & CRAWL4AI_FORBIDDEN_BROWSER_FIELDS)
    assert not (set(rparams) & CRAWL4AI_FORBIDDEN_RUN_FIELDS)
    out: dict[str, Any] = {}
    if bparams:
        out["browser_config"] = {"type": "BrowserConfig", "params": bparams}
    if rparams:
        out["crawler_config"] = {"type": "CrawlerRunConfig", "params": rparams}
    return out


# ── sandbox Chromium (CDP) ───────────────────────────────────────────────────

STEALTH_JS = r"""
(() => {
  try {
    const proto = Object.getPrototypeOf(navigator);
    if (navigator.webdriver) Object.defineProperty(proto, 'webdriver', {get: () => undefined, configurable: true});
    const langs = __LANGS__;
    if (langs.length) Object.defineProperty(proto, 'languages', {get: () => langs.slice(), configurable: true});
    if (!navigator.plugins || navigator.plugins.length === 0) {
      const fake = [{name: 'PDF Viewer', filename: 'internal-pdf-viewer', description: 'Portable Document Format'},
                    {name: 'Chrome PDF Viewer', filename: 'internal-pdf-viewer', description: 'Portable Document Format'}];
      Object.defineProperty(proto, 'plugins', {get: () => Object.assign(fake.slice(), {item: i => fake[i], namedItem: n => fake.find(p => p.name === n) || null, refresh: () => {}}), configurable: true});
    }
    if (!window.chrome) window.chrome = {runtime: {}};
    const q = window.navigator.permissions && window.navigator.permissions.query;
    if (q) window.navigator.permissions.query = (p) => (p && p.name === 'notifications') ? Promise.resolve({state: Notification.permission}) : q.call(window.navigator.permissions, p);
  } catch (e) {}
})();
"""


def stealth_script(locale: str) -> str:
    langs = [locale, locale.split("-")[0]] if locale and "-" in locale else ([locale] if locale else [])
    return STEALTH_JS.replace("__LANGS__", json.dumps(langs))


def clean_user_agent(ua: str) -> str:
    return re.sub(r"HeadlessChrome", "Chrome", ua or "")


def cdp_page_commands(cfg: BrowserConfig, current_ua: str = "") -> list[tuple[str, dict[str, Any]]]:
    """Ordered CDP commands for one page session. Empty when everything is off."""
    cmds: list[tuple[str, dict[str, Any]]] = []
    stealth = cfg.stealth
    ua = stealth.user_agent or (clean_user_agent(current_ua) if stealth.enabled and "HeadlessChrome" in (current_ua or "") else "")
    user_agent = ua or (current_ua if stealth.locale else "")
    if user_agent:
        # One override carries both, so the UA string and Accept-Language can
        # never disagree (a classic headless tell).
        params: dict[str, Any] = {"userAgent": user_agent}
        if stealth.locale:
            params["acceptLanguage"] = accept_language(stealth.locale)
        cmds.append(("Emulation.setUserAgentOverride", params))
    if stealth.locale:
        cmds.append(("Emulation.setLocaleOverride", {"locale": stealth.locale.replace("-", "_")}))
    if cfg.timezone:
        cmds.append(("Emulation.setTimezoneOverride", {"timezoneId": cfg.timezone}))
    if cfg.viewport.set:
        cmds.append(("Emulation.setDeviceMetricsOverride", {"width": cfg.viewport.width, "height": cfg.viewport.height, "deviceScaleFactor": 0, "mobile": False}))
    if stealth.enabled:
        # The script only runs for sessions with the Page domain enabled.
        cmds.append(("Page.enable", {}))
        cmds.append(("Page.addScriptToEvaluateOnNewDocument", {"source": stealth_script(stealth.locale)}))
    hosts = _blocking_hosts(cfg)
    if hosts:
        cmds.append(("Network.enable", {}))
        cmds.append(("Network.setBlockedURLs", {"urls": cdp_url_patterns(hosts)}))
    return cmds
