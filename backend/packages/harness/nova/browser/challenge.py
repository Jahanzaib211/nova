"""Bot-wall and captcha detection — explicit signals only.

The escalation ladder and the browser tools act on these, so a false positive
costs a needless slow retry and a false "needs human" interrupts the user. The
rules therefore look for vendor fingerprints (challenge script paths, widget
iframes, vendor headers), not for words like "verify" that ordinary pages use.
A 403/429/503 *alone* is not a bot wall: it needs a fingerprint, or an
essentially empty body, before it counts.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

# kind → needs a human (True) or can be retried with a stronger engine (False)
_CAPTCHA_KINDS = {"recaptcha", "hcaptcha", "turnstile", "captcha"}

_BODY_RULES: list[tuple[str, re.Pattern[str]]] = [
    ("turnstile", re.compile(r"challenges\.cloudflare\.com/turnstile|cf-turnstile", re.I)),
    ("recaptcha", re.compile(r"google\.com/recaptcha/|recaptcha/api\.js|class=[\"'][^\"']*g-recaptcha", re.I)),
    ("hcaptcha", re.compile(r"hcaptcha\.com/1/api\.js|class=[\"'][^\"']*h-captcha|newassets\.hcaptcha\.com", re.I)),
    ("cloudflare", re.compile(r"/cdn-cgi/challenge-platform/|cf-browser-verification|<title>\s*Just a moment\.\.\.\s*</title>|cf_chl_opt", re.I)),
    ("akamai", re.compile(r"_abck|/akam/\d+/|bm-verify|ak_bmsc", re.I)),
    ("perimeterx", re.compile(r"px-captcha|_pxCaptcha|perimeterx", re.I)),
    ("datadome", re.compile(r"captcha-delivery\.com|datadome", re.I)),
]
_HEADER_RULES: list[tuple[str, str, re.Pattern[str]]] = [
    ("cloudflare", "cf-mitigated", re.compile(r"challenge", re.I)),
    ("datadome", "x-datadome", re.compile(r".+")),
    ("akamai", "server", re.compile(r"AkamaiGHost", re.I)),
]
_BLOCK_STATUSES = {403, 429, 503}


@dataclass(frozen=True)
class BlockSignal:
    kind: str
    status: int | None
    reason: str

    @property
    def needs_human(self) -> bool:
        return self.kind in _CAPTCHA_KINDS

    def to_dict(self) -> dict:
        return {"kind": self.kind, "status": self.status, "reason": self.reason, "needs_human": self.needs_human}


def _visible_text_len(html: str) -> int:
    text = re.sub(r"(?is)<(script|style|noscript)[^>]*>.*?</\1>", " ", html)
    text = re.sub(r"(?s)<[^>]+>", " ", text)
    return len(re.sub(r"\s+", " ", text).strip())


def detect_block(status: int | None, headers: dict[str, str] | None, body: str | None) -> BlockSignal | None:
    """Classify a fetched response. Returns None for an ordinary page."""
    body = body or ""
    headers = {k.lower(): v for k, v in (headers or {}).items()}
    head = body[:200_000]

    for kind, pattern in _BODY_RULES:
        if pattern.search(head):
            # A captcha widget on a 200 page is still a wall when the page has
            # almost no content of its own; on a real article it is just a
            # comment-form widget and must not trigger anything.
            if status in _BLOCK_STATUSES or _visible_text_len(head) < 1500 or kind in {"cloudflare", "perimeterx", "datadome"}:
                return BlockSignal(kind, status, f"{kind} fingerprint in body")
    for kind, name, pattern in _HEADER_RULES:
        value = headers.get(name)
        if value and pattern.search(value) and (status in _BLOCK_STATUSES or name != "server"):
            return BlockSignal(kind, status, f"{name} header")
    if status in _BLOCK_STATUSES and _visible_text_len(head) < 200:
        return BlockSignal("rate_limited" if status == 429 else "bot_wall", status, f"HTTP {status} with an empty body")
    return None


# Run in the live page (browser tools): widget iframes + challenge pages.
CAPTCHA_PROBE_JS = r"""
(() => {
  const srcs = Array.from(document.querySelectorAll('iframe')).map(f => f.src || '');
  const has = (re) => srcs.some(s => re.test(s));
  const vis = (el) => { if (!el) return false; const r = el.getBoundingClientRect(); return r.width > 20 && r.height > 20; };
  let kind = null;
  if (has(/challenges\.cloudflare\.com/) || document.querySelector('.cf-turnstile')) kind = 'turnstile';
  else if (has(/google\.com\/recaptcha\/(api2|enterprise)\/(anchor|bframe)/) && Array.from(document.querySelectorAll('iframe[src*="recaptcha"]')).some(vis)) kind = 'recaptcha';
  else if (has(/hcaptcha\.com/) && Array.from(document.querySelectorAll('iframe[src*="hcaptcha"]')).some(vis)) kind = 'hcaptcha';
  else if (/^Just a moment/.test(document.title) || document.querySelector('#challenge-form, #cf-challenge-running')) kind = 'cloudflare';
  else if (document.querySelector('#px-captcha') || has(/captcha-delivery\.com/)) kind = 'captcha';
  return kind ? {kind, url: location.href, title: document.title} : null;
})()
"""


def probe_page(page) -> BlockSignal | None:
    """Run the probe in a Playwright page. Never raises."""
    try:
        found = page.evaluate(CAPTCHA_PROBE_JS)
    except Exception:  # noqa: BLE001 - a probe failure is "nothing detected"
        return None
    if not isinstance(found, dict) or not found.get("kind"):
        return None
    kind = str(found["kind"])
    if kind == "cloudflare":
        # An interstitial with no widget usually clears itself; report it as a
        # wall (retryable), not a captcha, so callers wait instead of paging the user.
        return BlockSignal("cloudflare", None, "challenge interstitial in page")
    return BlockSignal(kind, None, f"{kind} widget in page")
