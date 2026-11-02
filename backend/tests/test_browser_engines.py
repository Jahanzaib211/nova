"""One `browser:` config, mapped onto each engine's own API."""

from __future__ import annotations

import json

from nova.browser import blocklist, engines
from nova.browser.challenge import detect_block
from nova.browser.recording import redact_url, sanitize_action
from nova.config.browser_config import BrowserConfig


def _cfg(**kw) -> BrowserConfig:
    return BrowserConfig.model_validate(kw)


class TestDefaultsAreInert:
    def test_everything_off_by_default(self):
        cfg = BrowserConfig()
        assert engines.browserless_request(cfg) == ({}, {})
        assert engines.crawl4ai_configs(cfg) == {}
        assert engines.cdp_page_commands(cfg, "Mozilla/5.0 HeadlessChrome/140") == []


class TestBrowserless:
    def test_stealth_proxy_blocking_cookies(self, monkeypatch):
        monkeypatch.setenv("PROXY_PW", "pw-secret")
        cfg = _cfg(stealth={"enabled": True, "locale": "de-DE", "user_agent": "UA/1"}, viewport={"width": 1280, "height": 800}, proxy={"server": "http://p:3128", "username": "bob", "password_env": "PROXY_PW"}, blocking={"enabled": True})
        query, body = engines.browserless_request(cfg, cookies=[{"name": "a", "value": "b", "domain": ".x.com", "path": "/", "secure": True, "sameSite": "Lax", "expires": -1}])
        launch = json.loads(query["launch"])
        assert launch["stealth"] is True
        assert "--proxy-server=http://p:3128" in launch["args"] and "--lang=de-DE" in launch["args"]
        assert body["authenticate"] == {"username": "bob", "password": "pw-secret"}
        assert body["userAgent"] == {"userAgent": "UA/1"}
        assert body["setExtraHTTPHeaders"]["Accept-Language"].startswith("de-DE")
        assert body["viewport"] == {"width": 1280, "height": 800}
        assert any("doubleclick" in p for p in body["rejectRequestPattern"])
        assert body["cookies"] == [{"name": "a", "value": "b", "domain": ".x.com", "path": "/", "secure": True, "sameSite": "Lax"}]

    def test_ladder_can_force_stealth(self):
        query, _ = engines.browserless_request(BrowserConfig(), stealth=True)
        assert json.loads(query["launch"]) == {"stealth": True}

    def test_missing_password_env_degrades(self):
        cfg = _cfg(proxy={"server": "http://p:1", "username": "u", "password_env": "NOT_SET_ANYWHERE_123"})
        _, body = engines.browserless_request(cfg)
        assert body["authenticate"]["password"] == ""


class TestCrawl4ai:
    def test_only_allowlisted_fields(self):
        cfg = _cfg(stealth={"enabled": True, "locale": "fr-FR", "user_agent": "UA"}, timezone="Europe/Paris", viewport={"width": 1000, "height": 700}, blocking={"enabled": True}, proxy={"server": "http://p:1"})
        out = engines.crawl4ai_configs(cfg)
        b = out["browser_config"]["params"]
        r = out["crawler_config"]["params"]
        assert b == {"enable_stealth": True, "user_agent": "UA", "viewport_width": 1000, "viewport_height": 700, "avoid_ads": True}
        assert r == {"locale": "fr-FR", "timezone_id": "Europe/Paris"}
        # The server 400s on any of these from an untrusted request.
        assert not set(b) & engines.CRAWL4AI_FORBIDDEN_BROWSER_FIELDS
        assert not set(r) & engines.CRAWL4AI_FORBIDDEN_RUN_FIELDS


class TestCdp:
    def test_commands(self):
        cfg = _cfg(stealth={"enabled": True, "locale": "en-GB"}, timezone="Asia/Tokyo", viewport={"width": 800, "height": 600}, blocking={"enabled": True, "extra_hosts": ["ads.example"]})
        cmds = dict(engines.cdp_page_commands(cfg, "Mozilla/5.0 HeadlessChrome/140.0"))
        assert cmds["Emulation.setUserAgentOverride"]["userAgent"] == "Mozilla/5.0 Chrome/140.0"
        assert cmds["Emulation.setUserAgentOverride"]["acceptLanguage"].startswith("en-GB")
        assert cmds["Emulation.setTimezoneOverride"] == {"timezoneId": "Asia/Tokyo"}
        assert "webdriver" in cmds["Page.addScriptToEvaluateOnNewDocument"]["source"]
        assert "*://*.ads.example/*" in cmds["Network.setBlockedURLs"]["urls"]


class TestBlocklist:
    def test_subdomains(self):
        hosts = blocklist.blocked_hosts(extra=["evil.test"])
        assert blocklist.is_blocked("https://stats.g.doubleclick.net/x", hosts)
        assert blocklist.is_blocked("https://a.evil.test/", hosts)
        assert not blocklist.is_blocked("https://notdoubleclick.net/", hosts)


class TestChallengeDetection:
    def test_cloudflare_interstitial(self):
        s = detect_block(503, {"cf-mitigated": "challenge"}, "<html><title>Just a moment...</title><script src='/cdn-cgi/challenge-platform/x'></script></html>")
        assert s and s.kind == "cloudflare" and not s.needs_human

    def test_captcha_wall_needs_human(self):
        s = detect_block(403, {}, "<div class='g-recaptcha'></div><script src='https://www.google.com/recaptcha/api.js'></script>")
        assert s and s.kind == "recaptcha" and s.needs_human

    def test_article_with_a_comment_captcha_is_not_a_wall(self):
        body = "<article>" + ("real content " * 400) + "</article><script src='https://www.google.com/recaptcha/api.js'></script>"
        assert detect_block(200, {}, body) is None

    def test_plain_403_with_content_is_not_a_wall(self):
        assert detect_block(403, {}, "<h1>Forbidden</h1>" + "<p>You do not have access to this repository.</p>" * 10) is None

    def test_empty_429(self):
        s = detect_block(429, {}, "")
        assert s and s.kind == "rate_limited"

    def test_ordinary_page(self):
        assert detect_block(200, {}, "<html><body>hello</body></html>") is None


class TestRecordingRedaction:
    def test_redact_url(self):
        out = redact_url("https://u:p@x.com/cb?code=abc&state=1&access_token=zzz")
        assert "abc" not in out and "zzz" not in out and "u:p" not in out and "state=1" in out

    def test_sanitize_action(self):
        a = sanitize_action("browser_input", {"selector": "#pw", "text": "hunter2", "press_enter": True})
        assert a == {"tool": "browser_input", "selector": "#pw", "text_chars": 7, "press_enter": True}
