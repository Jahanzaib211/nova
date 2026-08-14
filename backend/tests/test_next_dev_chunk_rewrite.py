"""Regression tests for Next.js dev-mode ``self.__next_f.push(...)`` chunk
rewriting in the preview/appview HTML prefixer.

Pinned by the 2026-08-14 incident where the Browser tab's iframe rendered the
page but the CSS bundle and lazy ``_next/static/chunks/*.js`` chunks did not
load. The naive ``replace('href="/', ...)`` rewrite catches links that
Next.js bakes into the initial HTML, but dev mode emits chunk URLs **inside
the streaming payload** of ``self.__next_f.push([1, "...\"/_next/static/..."])``
— and those URLs are not attribute-anchored, so the prefix helper missed
them. The fix is a regex pass over the streaming payload; this test pins the
contract that ``"/_next/static/..."`` inside ``self.__next_f.push`` payloads
gets rewritten to the proxy prefix.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

_HERE = Path(__file__).resolve().parent
_BACKEND = _HERE.parent
if str(_BACKEND) not in sys.path:
    sys.path.insert(0, str(_BACKEND))


@pytest.fixture(scope="module")
def prefixer():
    from app.gateway.routers.sandbox import _prefix_html_urls

    return _prefix_html_urls


def test_prefixer_rewrites_literal_next_asset_urls(prefixer):
    html = '<link rel="stylesheet" href="/_next/static/css/app.css"/>'
    out = prefixer(html, "/api/sandbox/preview/t1")
    assert 'href="/api/sandbox/preview/t1/_next/static/css/app.css"' in out


def test_prefixer_rewrites_next_dev_flight_payload_chunks(prefixer):
    """Next.js dev mode streams its initial markup via ``self.__next_f.push``.
    The chunk URLs live inside a JSON-encoded string with embedded escapes,
    not as an HTML attribute. The naive replace-based prefixer misses these
    — and the Browser tab renders the page unstyled. This fixture mirrors the
    real shape and asserts the rewrite reaches inside the payload.
    """
    html = (
        '<script>self.__next_f.push([1, "'
        r"\"\"/_next/static/chunks/app/page-7b3c4d.js\""
        r"\\\"\"])"
        "])</script>"
    )
    out = prefixer(html, "/api/sandbox/preview/t1")
    # The literal ``\"/_next/static/...`` substring must be rewritten to the
    # proxy prefix so the Browser tab's iframe fetch hits the proxy.
    assert "/api/sandbox/preview/t1/_next/static/chunks/app/page-7b3c4d.js" in out
    # The opening escape and surrounding JS structure must remain intact
    # (otherwise the streaming payload would not parse in the browser).
    assert "self.__next_f.push" in out


def test_prefixer_is_idempotent_on_a_second_pass(prefixer):
    """The helper must not double-prefix on a re-run — the original
    ``TestPrefixHtmlUrls::test_is_idempotent_on_a_second_pass`` covers the
    same-prefix case; this one covers a different prefix (the worst case is
    when a response ever flows through two proxies)."""
    html = '<a href="/_next/static/x.js">x</a>'
    once = prefixer(html, "/api/sandbox/preview/t1")
    twice = prefixer(once, "/api/sandbox/appview/t1")
    # The second pass should NOT double-prefix the already-rewritten URL.
    # It will rewrite the *new* root-absolute URLs it finds, but the first
    # one stays at exactly one prefix.
    assert "/api/sandbox/preview/t1/_next/static/x.js" in twice
    # No ``/api/sandbox/preview/t1/api/sandbox/...`` double-prefixing.
    assert "/api/sandbox/preview/t1/api/sandbox/" not in twice


def test_prefixer_handles_quoted_chunk_urls(prefixer):
    """Next.js sometimes emits chunk URLs as bare quoted strings (not inside
    an attribute or a flight payload). The naive string replace for
    ``\"/_next/`` catches these too."""
    html = '<script>const u = "/_next/static/chunks/foo.js"; import(u)</script>'
    out = prefixer(html, "/api/sandbox/preview/t1")
    assert '"/api/sandbox/preview/t1/_next/static/chunks/foo.js"' in out
