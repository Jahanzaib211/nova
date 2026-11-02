"""Small bundled ad/tracker host list for `browser.blocking`.

Deliberately short: the goal is to drop the heaviest third-party ad and
analytics hosts (latency, fingerprinting, consent-wall bait), not to be a full
filter list — a long list breaks sites in ways an agent cannot diagnose.
Operators extend it with `browser.blocking.extra_hosts`. Subdomains of each
host are blocked too.
"""

from __future__ import annotations

from urllib.parse import urlparse

BUNDLED_HOSTS: tuple[str, ...] = (
    "doubleclick.net",
    "googlesyndication.com",
    "googleadservices.com",
    "google-analytics.com",
    "googletagmanager.com",
    "googletagservices.com",
    "adservice.google.com",
    "amazon-adsystem.com",
    "adnxs.com",
    "adsrvr.org",
    "advertising.com",
    "criteo.com",
    "criteo.net",
    "taboola.com",
    "outbrain.com",
    "pubmatic.com",
    "rubiconproject.com",
    "openx.net",
    "casalemedia.com",
    "moatads.com",
    "scorecardresearch.com",
    "quantserve.com",
    "hotjar.com",
    "mixpanel.com",
    "segment.io",
    "segment.com",
    "fullstory.com",
    "mouseflow.com",
    "crazyegg.com",
    "clarity.ms",
    "connect.facebook.net",
    "ads-twitter.com",
    "analytics.twitter.com",
    "ads.linkedin.com",
    "bat.bing.com",
    "adform.net",
    "smartadserver.com",
    "yieldmo.com",
    "sharethrough.com",
    "media.net",
    "zedo.com",
    "chartbeat.com",
    "newrelic.com",
    "nr-data.net",
)


def blocked_hosts(*, use_bundled: bool = True, extra: list[str] | None = None) -> list[str]:
    hosts = list(BUNDLED_HOSTS) if use_bundled else []
    for h in extra or []:
        h = str(h).strip().lower().lstrip(".")
        if h and h not in hosts:
            hosts.append(h)
    return hosts


def is_blocked(url: str, hosts: list[str]) -> bool:
    host = (urlparse(url).hostname or "").lower()
    return any(host == h or host.endswith("." + h) for h in hosts)


def cdp_url_patterns(hosts: list[str]) -> list[str]:
    """Patterns for CDP ``Network.setBlockedURLs`` (wildcard syntax)."""
    out: list[str] = []
    for h in hosts:
        out.extend((f"*://{h}/*", f"*://*.{h}/*"))
    return out


def regex_patterns(hosts: list[str]) -> list[str]:
    """Patterns for Browserless ``rejectRequestPattern`` (JS regex strings)."""
    return [r"^https?://([^/]*\.)?" + h.replace(".", r"\.") + r"(:\d+)?/" for h in hosts]
