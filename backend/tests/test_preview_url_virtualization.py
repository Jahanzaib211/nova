"""A proxied framework app must hydrate cleanly under the preview prefix (2026-10-31).

Reproduced against a real Next.js 14 dev server through ``_proxy_dev_server``:

* The server rewrite prefixed ``<a href="/">`` in the HTML, but a client
  component renders ``/``. React reported a hydration mismatch, discarded the
  document and re-rendered it with unprefixed stylesheet/font URLs -- the
  unstyled Browser tab and the dev overlay's "1 error".
* Next's router took its starting route from ``window.location`` (the proxy
  path), so the navbar's active-link markup differed from the server's too.
* ``Link: </_next/static/media/….woff2>; rel=preload`` headers were passed
  through, so the browser preloaded every font from the app shell's root: one
  404 per font on every preview.

The runtime shim is checked in a real Chromium where one is available.
"""

from __future__ import annotations

import glob
import json
import os

import pytest

from app.gateway.routers.sandbox import (
    _inject_ws_shim,
    _prefix_link_header,
    _url_shim,
)

PREFIX = "/api/sandbox/preview/t1"


class TestLinkHeader:
    def test_root_absolute_targets_are_prefixed(self):
        value = '</_next/static/media/a.woff2>; rel=preload; as="font", </_next/static/media/b.woff2>; rel=preload; as="font"'
        out = _prefix_link_header(value, PREFIX)
        assert out == f'<{PREFIX}/_next/static/media/a.woff2>; rel=preload; as="font", <{PREFIX}/_next/static/media/b.woff2>; rel=preload; as="font"'

    def test_absolute_and_protocol_relative_targets_are_left_alone(self):
        value = "<https://cdn.example/x.css>; rel=preload, <//cdn.example/y.css>; rel=preload"
        assert _prefix_link_header(value, PREFIX) == value

    def test_is_idempotent(self):
        once = _prefix_link_header("</a.js>; rel=preload", PREFIX)
        assert _prefix_link_header(once, PREFIX) == once

    def test_empty_inputs(self):
        assert _prefix_link_header("", PREFIX) == ""
        assert _prefix_link_header("</a.js>", "") == "</a.js>"


class TestShimInjection:
    def test_url_shim_runs_before_every_other_script(self):
        html = "<html><head><script src='/app.js'></script></head><body></body></html>"
        out = _inject_ws_shim(html, "/api/sandbox/preview-ws/t1", PREFIX)
        head = out[out.index("<head>") : out.index("/app.js")]
        assert head.index("E.getAttribute=") < head.index("window.WebSocket=W")

    def test_next_pages_get_their_own_path_as_location(self):
        next_page = "<html><head></head><body><script>self.__next_f.push([0])</script></body></html>"
        plain_page = "<html><head></head><body><p>hi</p></body></html>"
        assert "R=true" in _inject_ws_shim(next_page, "/ws", PREFIX)
        # A Vite app's full reload re-requests location.href; it must stay prefixed.
        assert "R=false" in _inject_ws_shim(plain_page, "/ws", PREFIX)

    def test_appview_keeps_its_frozen_prepend_only_shim(self):
        out = _inject_ws_shim("<html><head></head></html>", "/api/sandbox/appview-ws/t1", "")
        assert "E.getAttribute=" not in out

    def test_injection_is_idempotent(self):
        html = "<html><head></head><body></body></html>"
        once = _inject_ws_shim(html, "/ws", PREFIX)
        assert _inject_ws_shim(once, "/ws", PREFIX) == once


def _launch(pw):
    try:
        return pw.chromium.launch()
    except Exception:
        pass
    cached = sorted(glob.glob(os.path.expanduser("~/.cache/ms-playwright/chromium-*/chrome-linux64/chrome")))
    if not cached:
        pytest.skip("no Chromium available for Playwright")
    try:
        return pw.chromium.launch(executable_path=cached[-1])
    except Exception as e:  # pragma: no cover - environment dependent
        pytest.skip(f"Chromium would not start: {e}")


ORIGIN = "http://preview.test"


def _page_html(app_location: bool) -> str:
    # What the proxy serves: the shim first, then HTML whose URLs the server
    # rewrite has already prefixed.
    return (
        "<!doctype html><html><head>"
        + _url_shim(PREFIX, app_location=app_location)
        + f'<base href="{PREFIX}/"></head><body>'
        + f'<a id="home" href="{PREFIX}/">Home</a>'
        + f'<img id="logo" src="{PREFIX}/logo.png" srcset="{PREFIX}/logo.png 1x, {PREFIX}/logo@2x.png 2x">'
        + "</body></html>"
    )


@pytest.fixture(scope="module")
def browser():
    sync_api = pytest.importorskip("playwright.sync_api")
    with sync_api.sync_playwright() as pw:
        b = _launch(pw)
        yield b
        b.close()


def _open(browser, app_location: bool, path: str = "/"):
    page = browser.new_page()
    requested: list[str] = []

    def handle(route):
        url = route.request.url
        requested.append(url[len(ORIGIN) :])
        if url.startswith(f"{ORIGIN}{PREFIX}/") and route.request.resource_type == "document":
            route.fulfill(status=200, content_type="text/html", body=_page_html(app_location))
        else:
            route.fulfill(status=200, content_type="application/json", body=json.dumps({"ok": True}))

    page.route("**/*", handle)
    page.goto(f"{ORIGIN}{PREFIX}{path}")
    return page, requested


class TestRuntimeShim:
    def test_get_attribute_returns_the_apps_own_url(self, browser):
        # React's hydration compares getAttribute() with what the app renders.
        page, _ = _open(browser, app_location=False)
        assert page.evaluate("document.getElementById('home').getAttribute('href')") == "/"
        assert page.evaluate("document.getElementById('logo').getAttribute('src')") == "/logo.png"
        assert page.evaluate("document.getElementById('logo').getAttribute('srcset')") == "/logo.png 1x, /logo@2x.png 2x"
        page.close()

    def test_elements_the_app_creates_load_through_the_proxy(self, browser):
        page, _ = _open(browser, app_location=False)
        real = page.evaluate(
            """() => {
              const a = document.createElement('a'); a.setAttribute('href', '/contact');
              const l = document.createElement('link'); l.href = '/_next/static/css/x.css';
              const i = document.createElement('img'); i.src = '/a.png';
              const ext = document.createElement('a'); ext.setAttribute('href', 'https://example.com/');
              const raw = (el, n) => el.outerHTML.match(new RegExp(n + '="([^"]*)"'))[1];
              return [raw(a, 'href'), raw(l, 'href'), raw(i, 'src'), raw(ext, 'href')];
            }"""
        )
        assert real == [f"{PREFIX}/contact", f"{PREFIX}/_next/static/css/x.css", f"{PREFIX}/a.png", "https://example.com/"]
        page.close()

    def test_fetch_and_history_stay_under_the_prefix(self, browser):
        page, requested = _open(browser, app_location=False)
        page.evaluate("fetch('/contact?_rsc=1')")
        page.evaluate("fetch(new URL('/api/data', location.href))")
        page.wait_for_timeout(200)
        assert f"{PREFIX}/contact?_rsc=1" in requested
        assert f"{PREFIX}/api/data" in requested
        assert "/contact?_rsc=1" not in requested
        page.evaluate("history.pushState({}, '', '/services')")
        assert page.evaluate("location.pathname") == f"{PREFIX}/services"
        page.close()

    def test_next_router_starts_from_the_apps_own_path(self, browser):
        page, _ = _open(browser, app_location=True, path="/services")
        # Before the router re-attaches its state, the location is the app's path...
        assert page.evaluate("location.pathname") == "/services"
        # ...and its replaceState on mount puts the prefix back.
        page.evaluate("history.replaceState({__NA: true}, '', '/services')")
        assert page.evaluate("location.pathname") == f"{PREFIX}/services"
        page.close()

    def test_other_apps_keep_their_prefixed_location(self, browser):
        page, _ = _open(browser, app_location=False, path="/services")
        assert page.evaluate("location.pathname") == f"{PREFIX}/services"
        page.close()
