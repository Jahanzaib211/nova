"""`web_crawl` — the properties that make it a crawl and not a fetch loop.

The failure mode worth testing against is not an exception. It is a crawl that
runs, returns the start page, and reports success — indistinguishable from a
working crawler unless you assert that it reached a page nobody named.
"""

from __future__ import annotations

import asyncio
from typing import Any

import pytest

from deerflow.community.crawl4ai.crawl_tool import (
    _LinkExtractor,
    _normalise,
    _RobotsGate,
    _same_site,
    crawl_site,
)


def _page(url: str, links: list[str] | None = None, *, text: str = "body text") -> dict[str, Any]:
    """A Crawl4AI-shaped result whose links come from real anchor HTML."""
    anchors = "".join(f'<a href="{h}">x</a>' for h in (links or []))
    return {"url": url, "markdown": text, "html": f"<html><body>{anchors}</body></html>"}


class _FakeClient:
    """Records each round so tests can assert on crawl *shape*, not just output."""

    def __init__(self, pages: dict[str, dict[str, Any]]) -> None:
        self._pages = pages
        self.rounds: list[list[str]] = []

    async def crawl(self, urls: list[str], *, max_pages: int = 10) -> list[dict[str, Any]]:
        self.rounds.append(list(urls))
        return [self._pages[u] for u in urls[:max_pages] if u in self._pages]


@pytest.fixture
def allow_all_robots(monkeypatch):
    async def _allows(self, url: str, user_agent: str = "*") -> bool:
        return True

    monkeypatch.setattr(_RobotsGate, "allows", _allows)


def _install_client(monkeypatch, client: _FakeClient) -> None:
    monkeypatch.setattr("deerflow.community.crawl4ai.crawl_tool._get_client", lambda: client)


class TestItActuallyFollowsLinks:
    def test_reaches_a_page_nobody_named(self, monkeypatch, allow_all_robots):
        """The whole point. Every other web tool needs the URL up front."""
        client = _FakeClient(
            {
                "https://site.test/": _page("https://site.test/", ["/a", "/b"]),
                "https://site.test/a": _page("https://site.test/a"),
                "https://site.test/b": _page("https://site.test/b"),
            }
        )
        _install_client(monkeypatch, client)

        result = asyncio.run(crawl_site("https://site.test/", max_depth=1, max_pages=10))

        reached = {p["url"] for p in result["pages"]}
        assert reached == {"https://site.test/", "https://site.test/a", "https://site.test/b"}
        # Two rounds: the start page, then everything it linked to.
        assert client.rounds == [["https://site.test/"], ["https://site.test/a", "https://site.test/b"]]

    def test_depth_zero_is_the_start_page_alone(self, monkeypatch, allow_all_robots):
        client = _FakeClient({"https://site.test/": _page("https://site.test/", ["/a"])})
        _install_client(monkeypatch, client)

        result = asyncio.run(crawl_site("https://site.test/", max_depth=0))

        assert [p["url"] for p in result["pages"]] == ["https://site.test/"]
        assert len(client.rounds) == 1

    def test_records_the_link_graph(self, monkeypatch, allow_all_robots):
        """The graph is what separates crawl output from a pile of fetches."""
        client = _FakeClient({"https://site.test/": _page("https://site.test/", ["/a", "/b"])})
        _install_client(monkeypatch, client)

        result = asyncio.run(crawl_site("https://site.test/", max_depth=0))

        assert result["graph"]["https://site.test/"] == ["https://site.test/a", "https://site.test/b"]

    def test_a_page_reached_twice_is_fetched_once(self, monkeypatch, allow_all_robots):
        """Two pages linking to the same third must not spend two of the budget."""
        client = _FakeClient(
            {
                "https://site.test/": _page("https://site.test/", ["/a", "/b"]),
                "https://site.test/a": _page("https://site.test/a", ["/shared"]),
                "https://site.test/b": _page("https://site.test/b", ["/shared"]),
                "https://site.test/shared": _page("https://site.test/shared"),
            }
        )
        _install_client(monkeypatch, client)

        result = asyncio.run(crawl_site("https://site.test/", max_depth=2, max_pages=10))

        fetched = [u for round_ in client.rounds for u in round_]
        assert fetched.count("https://site.test/shared") == 1
        assert len(result["pages"]) == 4


class TestTheLimitsHold:
    def test_max_pages_truncates(self, monkeypatch, allow_all_robots):
        pages = {"https://site.test/": _page("https://site.test/", [f"/p{i}" for i in range(20)])}
        for i in range(20):
            pages[f"https://site.test/p{i}"] = _page(f"https://site.test/p{i}")
        _install_client(monkeypatch, _FakeClient(pages))

        result = asyncio.run(crawl_site("https://site.test/", max_depth=1, max_pages=5))

        assert len(result["pages"]) == 5

    def test_same_domain_excludes_offsite_links(self, monkeypatch, allow_all_robots):
        client = _FakeClient(
            {
                "https://site.test/": _page("https://site.test/", ["/inside", "https://elsewhere.test/out"]),
                "https://site.test/inside": _page("https://site.test/inside"),
                "https://elsewhere.test/out": _page("https://elsewhere.test/out"),
            }
        )
        _install_client(monkeypatch, client)

        result = asyncio.run(crawl_site("https://site.test/", max_depth=1, same_domain=True))

        assert "https://elsewhere.test/out" not in {p["url"] for p in result["pages"]}

    def test_same_domain_off_follows_offsite(self, monkeypatch, allow_all_robots):
        client = _FakeClient(
            {
                "https://site.test/": _page("https://site.test/", ["https://elsewhere.test/out"]),
                "https://elsewhere.test/out": _page("https://elsewhere.test/out"),
            }
        )
        _install_client(monkeypatch, client)

        result = asyncio.run(crawl_site("https://site.test/", max_depth=1, same_domain=False))

        assert "https://elsewhere.test/out" in {p["url"] for p in result["pages"]}

    def test_ceilings_clamp_absurd_arguments(self, monkeypatch, allow_all_robots):
        """The agent picks these numbers; the ceiling is not negotiable."""
        pages = {"https://site.test/": _page("https://site.test/")}
        _install_client(monkeypatch, _FakeClient(pages))

        result = asyncio.run(crawl_site("https://site.test/", max_depth=99, max_pages=10_000))

        assert result["stopped"] == "completed"

    def test_deadline_stops_the_crawl(self, monkeypatch):
        """One slow host must not hold the tool call open indefinitely."""

        async def _allows(self, url: str, user_agent: str = "*") -> bool:
            return True

        monkeypatch.setattr(_RobotsGate, "allows", _allows)

        class _SlowClient(_FakeClient):
            async def crawl(self, urls, *, max_pages=10):
                await asyncio.sleep(0.05)
                return await super().crawl(urls, max_pages=max_pages)

        client = _SlowClient(
            {
                "https://site.test/": _page("https://site.test/", ["/a"]),
                "https://site.test/a": _page("https://site.test/a", ["/b"]),
                "https://site.test/b": _page("https://site.test/b"),
            }
        )
        _install_client(monkeypatch, client)

        result = asyncio.run(crawl_site("https://site.test/", max_depth=3, deadline_s=0.01))

        assert result["stopped"] == "deadline"


class TestRobots:
    def test_disallowed_start_url_is_not_fetched(self, monkeypatch):
        """Ignoring robots is how a crawler gets the deployment's IP blocked."""

        async def _deny(self, url: str, user_agent: str = "*") -> bool:
            return False

        monkeypatch.setattr(_RobotsGate, "allows", _deny)
        client = _FakeClient({"https://site.test/": _page("https://site.test/")})
        _install_client(monkeypatch, client)

        result = asyncio.run(crawl_site("https://site.test/"))

        assert result["pages"] == []
        assert result["stopped"] == "robots"
        assert client.rounds == []  # never even asked the fetcher

    def test_disallowed_child_is_skipped_but_crawl_continues(self, monkeypatch):
        async def _selective(self, url: str, user_agent: str = "*") -> bool:
            return not url.endswith("/private")

        monkeypatch.setattr(_RobotsGate, "allows", _selective)
        client = _FakeClient(
            {
                "https://site.test/": _page("https://site.test/", ["/public", "/private"]),
                "https://site.test/public": _page("https://site.test/public"),
                "https://site.test/private": _page("https://site.test/private"),
            }
        )
        _install_client(monkeypatch, client)

        result = asyncio.run(crawl_site("https://site.test/", max_depth=1))

        assert {p["url"] for p in result["pages"]} == {"https://site.test/", "https://site.test/public"}
        assert "https://site.test/private" in result["blocked_by_robots"]

    def test_missing_robots_means_unrestricted(self):
        """Absent robots.txt is permission, not prohibition -- otherwise every
        host without one becomes uncrawlable."""
        gate = _RobotsGate()
        gate._parsers["https://site.test"] = None

        assert asyncio.run(gate.allows("https://site.test/anything")) is True


class TestLinkHandling:
    def test_relative_links_resolve_against_the_page(self):
        assert _normalise("https://site.test/docs/intro", "../api") == "https://site.test/api"

    def test_fragments_are_stripped(self):
        """`/docs#install` and `/docs` are one fetch, not two of the budget."""
        assert _normalise("https://site.test/", "/docs#install") == "https://site.test/docs"

    @pytest.mark.parametrize("href", ["#top", "mailto:a@b.test", "javascript:void(0)", "tel:+1", "data:text/plain,x"])
    def test_non_http_schemes_are_rejected(self, href):
        assert _normalise("https://site.test/", href) is None

    @pytest.mark.parametrize("href", ["/a.pdf", "/img.PNG", "/app.js", "/font.woff2"])
    def test_binary_and_asset_links_are_skipped(self, href):
        assert _normalise("https://site.test/", href) is None

    def test_www_is_the_same_site(self):
        """Otherwise same_domain=True stops a crawl at its own front page."""
        assert _same_site("https://site.test/a", "https://www.site.test/b") is True

    def test_extractor_survives_broken_markup(self):
        parser = _LinkExtractor()
        parser.feed('<a href="/ok">x<a href=/bare><div><a>no href</a>')
        assert "/ok" in parser.hrefs

    def test_falls_back_to_html_when_the_service_sends_no_links(self, monkeypatch, allow_all_robots):
        """Returning no links must mean "no links", not "links field absent"."""
        client = _FakeClient(
            {
                "https://site.test/": _page("https://site.test/", ["/a"]),
                "https://site.test/a": _page("https://site.test/a"),
            }
        )
        _install_client(monkeypatch, client)

        result = asyncio.run(crawl_site("https://site.test/", max_depth=1))

        assert "https://site.test/a" in {p["url"] for p in result["pages"]}


class TestFailure:
    def test_a_failing_round_ends_the_crawl_without_raising(self, monkeypatch, allow_all_robots):
        class _Broken(_FakeClient):
            async def crawl(self, urls, *, max_pages=10):
                raise RuntimeError("crawl4ai unreachable")

        _install_client(monkeypatch, _Broken({}))

        result = asyncio.run(crawl_site("https://site.test/"))

        assert result["stopped"].startswith("error:")
        assert result["pages"] == []

    def test_partial_results_survive_a_later_failure(self, monkeypatch, allow_all_robots):
        class _FailsSecondRound(_FakeClient):
            async def crawl(self, urls, *, max_pages=10):
                if self.rounds:
                    raise RuntimeError("gone")
                return await super().crawl(urls, max_pages=max_pages)

        client = _FailsSecondRound(
            {
                "https://site.test/": _page("https://site.test/", ["/a"]),
                "https://site.test/a": _page("https://site.test/a"),
            }
        )
        _install_client(monkeypatch, client)

        result = asyncio.run(crawl_site("https://site.test/", max_depth=1))

        assert [p["url"] for p in result["pages"]] == ["https://site.test/"]
        assert result["stopped"].startswith("error:")
