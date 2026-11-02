"""Unified browser-layer settings (`browser:` in config.yaml).

One section drives every engine Nova browses with: the sandbox's own Chromium
(Playwright over CDP), Browserless (`web_fetch`) and Crawl4AI
(`web_fetch_many` / `web_crawl`). Each engine maps these settings onto what its
own API can express — see `nova.browser.engines`.

Every behaviour-changing knob defaults OFF, so an install without a `browser:`
section browses exactly as before. The two exceptions are additive and only
fire when the user created state for them: a saved profile is injected into
matching sites (`profiles.inject_into_fetch`), and a click that triggers a
download saves it into the thread's outputs (`downloads.enabled`).

Secrets are never written into this section: the proxy password is the *name*
of an environment variable (`password_env`), resolved at use time, so a
missing variable degrades to "no proxy auth" instead of failing config load
the way a `$VAR` reference does.
"""

from __future__ import annotations

import os

from pydantic import BaseModel, Field


class BrowserStealthConfig(BaseModel):
    enabled: bool = Field(default=False, description="Hide automation tells (navigator.webdriver, empty plugins, headless UA) and keep UA/locale/timezone/viewport consistent.")
    user_agent: str = Field(default="", description="User-Agent to present. Empty keeps the engine's own UA, minus any 'Headless' token.")
    locale: str = Field(default="", description="BCP-47 locale, e.g. en-US. Also drives Accept-Language and navigator.languages.")


class BrowserViewportConfig(BaseModel):
    width: int = Field(default=0, ge=0, le=4000)
    height: int = Field(default=0, ge=0, le=4000)

    @property
    def set(self) -> bool:
        return self.width > 0 and self.height > 0


class BrowserProxyConfig(BaseModel):
    server: str = Field(default="", description="Proxy URL, e.g. http://proxy.internal:3128. Empty = no proxy.")
    username: str = Field(default="")
    password_env: str = Field(default="", description="NAME of the env var holding the proxy password (never the password itself).")
    bypass: str = Field(default="", description="Comma-separated hosts that skip the proxy.")

    @property
    def enabled(self) -> bool:
        return bool(self.server.strip())

    def password(self) -> str:
        return os.environ.get(self.password_env, "") if self.password_env else ""


class BrowserBlockingConfig(BaseModel):
    enabled: bool = Field(default=False, description="Abort requests to ad/tracker hosts.")
    use_bundled_list: bool = Field(default=True, description="Include Nova's small bundled ad/tracker host list.")
    extra_hosts: list[str] = Field(default_factory=list, description="Additional hosts to block (subdomains included).")


class BrowserProfilesConfig(BaseModel):
    enabled: bool = Field(default=True, description="Allow saving/loading persistent browser profiles (cookies + localStorage, encrypted at rest).")
    auto_load_default: bool = Field(default=True, description="Load the user's default profile the first time a browser tool attaches in a thread.")
    inject_into_fetch: bool = Field(default=True, description="Send matching profile cookies with web_fetch / web_fetch_many / web_crawl (per-call opt-out: use_profile=false).")
    max_profiles: int = Field(default=20, ge=1, le=200)
    max_bytes: int = Field(default=1_000_000, ge=10_000, le=10_000_000, description="Cap on one profile's serialized storage state.")


class BrowserDownloadsConfig(BaseModel):
    enabled: bool = Field(default=True, description="Save downloads triggered in the sandbox browser to /mnt/user-data/outputs/downloads/.")
    wait_s: float = Field(default=20.0, ge=1.0, le=300.0, description="How long a click waits for a download it triggered to finish.")


class BrowserRecordingConfig(BaseModel):
    enabled: bool = Field(default=False, description="Record browser tool calls (screencast frames + action log). A thread can turn it on with the Activity-panel toggle even when this is false.")
    fps: float = Field(default=2.0, gt=0, le=10.0)
    quality: int = Field(default=55, ge=10, le=95, description="JPEG quality of recorded frames.")
    max_width: int = Field(default=1024, ge=320, le=1920)
    max_frames: int = Field(default=600, ge=10, le=10_000, description="Per-session frame cap.")
    max_bytes: int = Field(default=40_000_000, ge=1_000_000, le=500_000_000, description="Per-session byte cap.")
    keep_sessions: int = Field(default=10, ge=1, le=200, description="Recordings kept per thread; older ones are deleted.")


class BrowserEscalationConfig(BaseModel):
    enabled: bool = Field(default=True, description="On an explicit bot-wall signal, web_fetch retries with stealth, then in the sandbox browser with the user's profile.")
    use_sandbox_browser: bool = Field(default=True, description="Allow the last rung (sandbox Chromium). Only used when the thread already has a sandbox.")
    deadline_s: float = Field(default=75.0, ge=5.0, le=300.0, description="Wall-clock budget for the whole ladder.")


class BrowserConfig(BaseModel):
    stealth: BrowserStealthConfig = Field(default_factory=BrowserStealthConfig)
    timezone: str = Field(default="", description="IANA timezone to emulate, e.g. Europe/Berlin. Empty = engine default.")
    viewport: BrowserViewportConfig = Field(default_factory=BrowserViewportConfig)
    proxy: BrowserProxyConfig = Field(default_factory=BrowserProxyConfig)
    blocking: BrowserBlockingConfig = Field(default_factory=BrowserBlockingConfig)
    profiles: BrowserProfilesConfig = Field(default_factory=BrowserProfilesConfig)
    downloads: BrowserDownloadsConfig = Field(default_factory=BrowserDownloadsConfig)
    recording: BrowserRecordingConfig = Field(default_factory=BrowserRecordingConfig)
    escalation: BrowserEscalationConfig = Field(default_factory=BrowserEscalationConfig)
    captcha_detection: bool = Field(default=True, description="Detect reCAPTCHA/hCaptcha/Turnstile/challenge pages and hand them to the user (no third-party solvers).")


def get_browser_config() -> BrowserConfig:
    """The live `browser:` section, or defaults when config is unavailable.

    Read per call so config.yaml edits hot-reload; never raises, because a
    browsing tool must not fail on a config problem it cannot fix.
    """
    try:
        from nova.config import get_app_config

        cfg = getattr(get_app_config(), "browser", None)
        if isinstance(cfg, BrowserConfig):
            return cfg
        if isinstance(cfg, dict):
            return BrowserConfig.model_validate(cfg)
    except Exception:  # noqa: BLE001 - defaults are always a safe answer
        pass
    return BrowserConfig()
