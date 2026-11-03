"""web_fetch / web_fetch_many / web_crawl on top of the browser layer.

Profile cookies reach Browserless for matching sites only; Crawl4AI never sees
a cookie (its REST boundary 400s them) — profile-matched URLs are routed to
Browserless instead. The escalation ladder climbs only on explicit block
signals and reports which engine served the page.
"""

from __future__ import annotations

import asyncio
import json
from typing import Any

import pytest

from nova.browser import escalation
from nova.browser import profiles as P
from nova.browser.challenge import BlockSignal
from nova.community.browserless import tools as bl_tools
from nova.community.crawl4ai import routing
from nova.config.browser_config import BrowserConfig

CF = "<html><head><title>Just a moment...</title></head><body><script src='/cdn-cgi/challenge-platform/h/b'></script></body></html>"
OK = "<html><body><article><h1>Hello</h1><p>" + ("Real article text. " * 40) + "</p></article></body></html>"


class FakeBrowserless:
    def __init__(self, responses: list[tuple[str, int | None]]):
        self.responses = list(responses)
        self.calls: list[dict[str, Any]] = []
        self.last_target_status = None
        self.last_headers: dict[str, str] = {}

    async def fetch_html(self, url, query=None, extra_body=None, **kw):
        self.calls.append({"url": url, "query": query, "body": extra_body})
        html, status = self.responses.pop(0)
        self.last_target_status = status
        return html


def run(coro):
    return asyncio.run(coro)


class TestLadder:
    def test_no_block_is_one_step(self):
        c = FakeBrowserless([(OK, 200)])
        r = run(escalation.fetch_with_ladder("https://x.test/", c, {}, BrowserConfig()))
        assert r.engine == "browserless" and len(r.steps) == 1 and r.block is None
        assert c.calls[0]["query"] is None  # defaults send nothing new

    def test_block_escalates_to_stealth(self):
        c = FakeBrowserless([(CF, 503), (OK, 200)])
        r = run(escalation.fetch_with_ladder("https://x.test/?token=s3cret", c, {}, BrowserConfig()))
        assert r.engine == "browserless+stealth" and r.html == OK
        assert json.loads(c.calls[1]["query"]["launch"]) == {"stealth": True}
        assert [s["engine"] for s in r.steps] == ["browserless", "browserless+stealth"]
        assert r.steps[0]["block"] == "cloudflare"
        assert "ladder" in r.summary()

    def test_sandbox_is_the_last_rung(self):
        c = FakeBrowserless([(CF, 503), (CF, 503)])
        seen = []

        async def sandbox(url):
            seen.append(url)
            return {"html": OK, "block": None}

        r = run(escalation.fetch_with_ladder("https://x.test/", c, {}, BrowserConfig(), sandbox_fetch=sandbox))
        assert r.engine == "sandbox" and seen == ["https://x.test/"] and len(r.steps) == 3

    def test_no_sandbox_keeps_the_last_answer(self):
        c = FakeBrowserless([(CF, 503), (CF, 503)])

        async def none(url):
            return None

        r = run(escalation.fetch_with_ladder("https://x.test/", c, {}, BrowserConfig(), sandbox_fetch=none))
        assert r.engine == "browserless+stealth" and r.block is not None and r.steps[-1]["error"]

    def test_transport_error_does_not_escalate(self):
        c = FakeBrowserless([("Error: Browserless HTTP 500: boom", None)])
        r = run(escalation.fetch_with_ladder("https://x.test/", c, {}, BrowserConfig()))
        assert r.error.startswith("Error:") and len(c.calls) == 1

    def test_ladder_off(self):
        cfg = BrowserConfig.model_validate({"escalation": {"enabled": False}})
        c = FakeBrowserless([(CF, 503)])
        r = run(escalation.fetch_with_ladder("https://x.test/", c, {}, cfg))
        assert len(c.calls) == 1 and r.block is not None

    def test_captcha_on_sandbox_rung_needs_human(self):
        c = FakeBrowserless([(CF, 503), (CF, 503)])

        async def sandbox(url):
            return {"html": "<div class=g-recaptcha>", "block": BlockSignal("recaptcha", None, "widget")}

        r = run(escalation.fetch_with_ladder("https://x.test/", c, {}, BrowserConfig(), sandbox_fetch=sandbox))
        assert r.block.needs_human


@pytest.fixture
def profile_user(tmp_path, monkeypatch):
    monkeypatch.delenv(P.PROFILE_SECRET_ENV, raising=False)
    monkeypatch.setattr(P, "_base_dir", lambda: tmp_path)
    state = {"cookies": [{"name": "sid", "value": "VAL", "domain": ".private.test", "path": "/", "secure": True, "sameSite": "Lax", "expires": -1}], "origins": []}
    P.ProfileStore("u1", base_dir=tmp_path).save("p", state, ["private.test"])
    return "u1"


class TestWebFetchTool:
    def test_profile_cookies_and_engine_note(self, monkeypatch, profile_user):
        c = FakeBrowserless([(OK, 200)])
        monkeypatch.setattr(bl_tools, "_get_browserless_client", lambda: c)
        monkeypatch.setattr(bl_tools, "_get_tool_config", lambda name: None)
        monkeypatch.setattr(bl_tools, "_fetch_user_id", lambda rt: profile_user)
        out = run(bl_tools.web_fetch_tool.ainvoke({"url": "https://app.private.test/inbox"}))
        assert c.calls[0]["body"]["cookies"][0]["name"] == "sid"
        assert "saved browser profile" in out

    def test_opt_out_and_foreign_domain(self, monkeypatch, profile_user):
        c = FakeBrowserless([(OK, 200), (OK, 200)])
        monkeypatch.setattr(bl_tools, "_get_browserless_client", lambda: c)
        monkeypatch.setattr(bl_tools, "_get_tool_config", lambda name: None)
        monkeypatch.setattr(bl_tools, "_fetch_user_id", lambda rt: profile_user)
        run(bl_tools.web_fetch_tool.ainvoke({"url": "https://app.private.test/", "use_profile": False}))
        run(bl_tools.web_fetch_tool.ainvoke({"url": "https://elsewhere.test/"}))
        assert all(call["body"] is None or "cookies" not in call["body"] for call in c.calls)

    def test_needs_human_result(self, monkeypatch):
        c = FakeBrowserless([("<script src='https://www.google.com/recaptcha/api.js'></script>", 403), ("<script src='https://www.google.com/recaptcha/api.js'></script>", 403)])
        monkeypatch.setattr(bl_tools, "_get_browserless_client", lambda: c)
        monkeypatch.setattr(bl_tools, "_get_tool_config", lambda name: None)
        out = run(bl_tools.web_fetch_tool.ainvoke({"url": "https://walled.test/"}))
        assert out.startswith("NEEDS_HUMAN") and '"kind": "recaptcha"' in out


class FakeCrawl:
    def __init__(self):
        self.calls: list[tuple[list[str], dict]] = []

    async def crawl(self, urls, *, max_pages=10, extra=None):
        self.calls.append((list(urls), extra or {}))
        return [{"url": u, "markdown": f"md {u}"} for u in urls]


class TestCrawlRouting:
    def test_profile_urls_go_to_browserless_and_never_to_crawl4ai(self, monkeypatch, profile_user):
        c = FakeBrowserless([(OK, 200)])
        monkeypatch.setattr(bl_tools, "_get_browserless_client", lambda: c)
        crawl = FakeCrawl()
        out = run(routing.crawl_routed(crawl, ["https://public.test/a", "https://private.test/b"], max_pages=5, user_id=profile_user))
        assert [o["url"] for o in out] == ["https://public.test/a", "https://private.test/b"]
        assert out[1]["served_by"] == "browserless+profile"
        assert crawl.calls == [(["https://public.test/a"], {})]
        assert c.calls[0]["body"]["cookies"][0]["name"] == "sid"
        assert "cookies" not in json.dumps(crawl.calls)

    def test_browserless_failure_falls_back_anonymously(self, monkeypatch, profile_user):
        c = FakeBrowserless([("Error: down", None)])
        monkeypatch.setattr(bl_tools, "_get_browserless_client", lambda: c)
        crawl = FakeCrawl()
        out = run(routing.crawl_routed(crawl, ["https://private.test/b"], max_pages=5, user_id=profile_user))
        assert out[0]["markdown"] == "md https://private.test/b"

    def test_settings_reach_crawl4ai(self, monkeypatch):
        cfg = BrowserConfig.model_validate({"stealth": {"enabled": True}, "timezone": "UTC"})
        monkeypatch.setattr(routing, "get_browser_config", lambda: cfg)
        crawl = FakeCrawl()
        run(routing.crawl_routed(crawl, ["https://public.test/"], max_pages=5, user_id=None))
        extra = crawl.calls[0][1]
        assert extra["browser_config"]["params"] == {"enable_stealth": True}
        assert extra["crawler_config"]["params"] == {"timezone_id": "UTC"}
