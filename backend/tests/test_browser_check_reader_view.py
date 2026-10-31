"""The self-test and the screenshot tool must see what a reader sees (2026-10-31).

Three failures, all on a page that rendered perfectly in Firefox and Chromium:

* A full-page screenshot never scrolled, so every IntersectionObserver reveal
  below the fold photographed as an empty band and every count-up as ``0``.
* The self-test failed on ``Failed to load resource: ... 404`` with no URL --
  on a Next.js dev server, the browser's own automatic ``/favicon.ico`` probe.
* Content that genuinely never became visible passed the self-test, because
  only "no text at all" counted as broken.

The JavaScript is exercised in a real headless Chromium where one is available;
the callbacks are plain functions and are tested directly.
"""

from __future__ import annotations

import glob
import os
from types import SimpleNamespace

import pytest

from deerflow.sandbox.browser_check import (
    _HIDDEN_AREA_FAIL_RATIO,
    _HIDDEN_AREA_JS,
    _record_console_error,
    _record_failed_response,
    scroll_through,
)


class TestErrorRecording:
    def test_urlless_resource_line_is_dropped(self) -> None:
        errors: list[str] = []
        _record_console_error(errors, SimpleNamespace(type="error", text="Failed to load resource: the server responded with a status of 404 (Not Found)"))
        assert errors == []

    def test_real_console_errors_are_kept(self) -> None:
        errors: list[str] = []
        _record_console_error(errors, SimpleNamespace(type="error", text="TypeError: x is undefined"))
        _record_console_error(errors, SimpleNamespace(type="warning", text="deprecated"))
        assert errors == ["error: TypeError: x is undefined"]

    def test_failed_request_is_reported_with_its_url(self) -> None:
        errors: list[str] = []
        resp = SimpleNamespace(status=404, url="http://localhost:4100/_next/static/css/app.css", request=SimpleNamespace(method="GET"))
        _record_failed_response(errors, resp)
        assert errors == ["404 GET http://localhost:4100/_next/static/css/app.css"]

    def test_missing_favicon_is_not_an_app_error(self) -> None:
        errors: list[str] = []
        for url in ("http://localhost:4100/favicon.ico", "http://localhost:4100/favicon.ico?v=2"):
            _record_failed_response(errors, SimpleNamespace(status=404, url=url, request=SimpleNamespace(method="GET")))
        assert errors == []

    def test_successful_responses_are_ignored(self) -> None:
        errors: list[str] = []
        _record_failed_response(errors, SimpleNamespace(status=200, url="http://x/", request=SimpleNamespace(method="GET")))
        _record_failed_response(errors, SimpleNamespace(status=304, url="http://x/a.js", request=SimpleNamespace(method="GET")))
        assert errors == []


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


# A tall page whose lower sections are hidden until they intersect the
# viewport -- the pattern every scroll-reveal landing page uses. The script is
# wrapped because set_content reuses the window: a top-level `const` would be
# a redeclaration on the second load and the observer would never run.
_REVEAL_PAGE = """
<!doctype html><html><head><style>
  body { margin: 0; font: 16px sans-serif; }
  section { height: 900px; border-bottom: 1px solid #ccc; }
  .pending { opacity: 0; }
</style></head><body>
<section>Hero</section>
<section class="r pending">One</section>
<section class="r pending">Two</section>
<section class="r pending">Three</section>
<section class="r pending"><span id="count">0</span></section>
<script>
(() => {
  const io = new IntersectionObserver((es) => es.forEach((e) => {
    if (e.isIntersecting) {
      e.target.classList.remove('pending');
      const c = e.target.querySelector('#count'); if (c) c.textContent = '240';
      io.unobserve(e.target);
    }
  }));
  document.querySelectorAll('.r').forEach((el) => io.observe(el));
})();
</script></body></html>
"""

_NEVER_VISIBLE_PAGE = """
<!doctype html><html><head><style>
  body { margin: 0; } section { height: 900px; }
</style></head><body>
<section>Hero</section>
<section style="opacity:0">Broken one</section>
<section style="opacity:0">Broken two</section>
</body></html>
"""


@pytest.fixture(scope="module")
def page():
    sync_api = pytest.importorskip("playwright.sync_api")
    with sync_api.sync_playwright() as pw:
        browser = _launch(pw)
        pg = browser.new_page(viewport={"width": 1280, "height": 800})
        yield pg
        browser.close()


class TestReaderView:
    def test_scrolling_through_reveals_scroll_driven_content(self, page) -> None:
        page.set_content(_REVEAL_PAGE)
        assert page.evaluate("document.querySelectorAll('.pending').length") == 4
        scroll_through(page)
        assert page.evaluate("document.querySelectorAll('.pending').length") == 0
        assert page.inner_text("#count") == "240"
        # Back at the top, so a full-page capture starts where a reader does.
        assert page.evaluate("window.scrollY") == 0

    def test_revealed_page_is_not_flagged_as_hidden(self, page) -> None:
        page.set_content(_REVEAL_PAGE)
        scroll_through(page)
        assert page.evaluate(_HIDDEN_AREA_JS) < _HIDDEN_AREA_FAIL_RATIO

    def test_content_that_never_appears_is_flagged(self, page) -> None:
        page.set_content(_NEVER_VISIBLE_PAGE)
        scroll_through(page)
        assert page.evaluate(_HIDDEN_AREA_JS) >= _HIDDEN_AREA_FAIL_RATIO
