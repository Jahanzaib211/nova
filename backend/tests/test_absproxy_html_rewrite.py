"""The absproxy must rewrite HTML asset URLs, exactly like the preview proxy.

Regression for the Browser tab rendering a site as unstyled HTML (2026-08-21).

There are two proxy paths into a sandbox dev server and only one used to rewrite
HTML. ``_proxy_dev_server`` (the ``preview``/``lpreview`` path) injects a
``<base>`` and runs ``_prefix_html_urls``; ``_absproxy_impl`` rewrote only the
``Location`` response header. So a document served through absproxy loaded, and
every ``/_next/static/...`` request resolved against the app origin instead of
the proxy prefix and 404'd — the page rendered with no CSS at all.

That path is not exotic: the Browser tab falls back to absproxy whenever the
canonical preview proxy cannot reach the server, which is the normal case for a
dev server the agent started with raw ``bash`` rather than ``start_dev_server``.
The safety net caught the request and then served it unusably.

These tests pin the rewriting itself rather than the HTTP plumbing — the helper
is where the contract lives, and it is shared by both proxies.
"""

from __future__ import annotations

from app.gateway.routers.sandbox import _prefix_html_urls

PREFIX = "/api/sandbox/absproxy/t-123/3210"


class TestPrefixHtmlUrls:
    def test_rewrites_next_static_assets(self):
        """The exact shape that broke: Next.js inlines its asset base with no attribute."""
        html = '<html><head><link rel="stylesheet" href="/_next/static/css/app.css"></head></html>'

        out = _prefix_html_urls(html, PREFIX)

        assert f'href="{PREFIX}/_next/static/css/app.css"' in out
        assert 'href="/_next/' not in out

    def test_rewrites_attributeless_next_base(self):
        """Next inlines `"/_next/...` inside its bootstrap JSON, with no href=/src=."""
        html = '<script>{"assetPrefix":"","buildId":"x","path":"/_next/static/chunks/main.js"}</script>'

        out = _prefix_html_urls(html, PREFIX)

        assert f'"{PREFIX}/_next/static/chunks/main.js"' in out

    def test_rewrites_src_and_action(self):
        html = '<img src="/logo.png"><form action="/submit"></form>'

        out = _prefix_html_urls(html, PREFIX)

        assert f'src="{PREFIX}/logo.png"' in out
        assert f'action="{PREFIX}/submit"' in out

    def test_is_idempotent(self):
        """Load-bearing: the same page can be proxied twice, or arrive already
        prefixed because the browser resolved a link against <base>. Double
        prefixing would 404 just as surely as no prefixing."""
        html = '<link href="/_next/static/css/app.css">'

        once = _prefix_html_urls(html, PREFIX)
        twice = _prefix_html_urls(once, PREFIX)

        assert once == twice
        assert f"{PREFIX}{PREFIX}" not in twice

    def test_leaves_absolute_and_relative_urls_alone(self):
        """Only root-absolute URLs are ambiguous. A protocol-absolute URL points
        somewhere else entirely, and a relative one already resolves correctly
        under <base>."""
        html = '<a href="https://example.com/x"></a><img src="./logo.png">'

        out = _prefix_html_urls(html, PREFIX)

        assert 'href="https://example.com/x"' in out
        assert 'src="./logo.png"' in out

    def test_empty_prefix_is_a_noop(self):
        html = '<link href="/a.css">'

        assert _prefix_html_urls(html, "") == html


class TestAbsproxyAppliesTheRewrite:
    """The rewrite must actually be wired into the absproxy handler, not just
    available to it — that was the whole bug."""

    def test_handler_injects_base_and_prefixes_urls(self):
        import inspect

        from app.gateway.routers import sandbox

        source = inspect.getsource(sandbox._absproxy_impl)

        assert "_prefix_html_urls" in source, "absproxy must rewrite asset URLs"
        assert "<base " in source, "absproxy must inject a <base> for relative URLs"
        assert "text/html" in source, "the rewrite must be gated on HTML responses"


# ── Gaps found while chasing "renders fine in Chrome, wrong inside Nova" ──────
#
# None of these was the cause of that particular report, but each is a real hole
# in the rewriter that would silently corrupt some other build.

import re  # noqa: E402

from app.gateway.routers.sandbox import _prefix_css_urls  # noqa: E402


class TestSrcsetIsRewrittenInFull:
    """Only the FIRST srcset entry sits behind a quote; the rest follow a comma.

    A responsive image then 404s on exactly the descriptors the browser picks for
    other device-pixel-ratios and viewports — "fine on my screen, broken on
    yours", which is close to undiagnosable from a bug report.
    """

    def test_every_entry_is_prefixed(self):
        html = '<img srcset="/_next/image?url=a&w=640 640w, /_next/image?url=a&w=1080 1080w, /x.png 2x">'
        out = _prefix_html_urls(html, PREFIX)
        entries = re.search(r'srcset="([^"]*)"', out).group(1).split(",")
        assert len(entries) == 3
        for entry in entries:
            assert entry.strip().startswith(f"{PREFIX}/"), f"unprefixed entry: {entry!r}"

    def test_single_quoted_srcset(self):
        html = "<img srcset='/a.png 1x, /b.png 2x'>"
        out = _prefix_html_urls(html, PREFIX)
        assert out.count(f"{PREFIX}/") == 2

    def test_imagesrcset_preload_is_covered(self):
        html = '<link rel="preload" imagesrcset="/a.png 1x, /b.png 2x">'
        out = _prefix_html_urls(html, PREFIX)
        assert out.count(f"{PREFIX}/") == 2

    def test_already_prefixed_srcset_is_not_doubled(self):
        html = f'<img srcset="{PREFIX}/a.png 1x, {PREFIX}/b.png 2x">'
        assert _prefix_html_urls(html, PREFIX) == html


class TestSingleQuotedAttributes:
    """The rewriter was pure double-quote string replacement."""

    def test_href_src_action(self):
        html = "<a href='/about'></a><img src='/x.png'><form action='/submit'></form>"
        out = _prefix_html_urls(html, PREFIX)
        assert out.count(f"{PREFIX}/") == 3
        assert "href='/about'" not in out

    def test_next_asset_base_single_quoted(self):
        html = "<script>a='/_next/static/chunks/main.js'</script>"
        out = _prefix_html_urls(html, PREFIX)
        assert f"'{PREFIX}/_next/" in out


class TestStylesheetUrls:
    """CSS was never rewritten: the call sites gate on ``text/html``.

    A ``url(/_next/static/media/…)`` — a web font or a background image — then
    resolved against the app origin and 404'd, leaving the page styled just
    enough to look almost right.
    """

    def test_bare_quoted_and_single_quoted_urls(self):
        css = "a{background:url(/bg.png)}b{background:url(\"/x.png\")}c{src:url('/y.woff2')}"
        out = _prefix_css_urls(css, PREFIX)
        assert out.count(f"{PREFIX}/") == 3
        assert "url(/bg.png)" not in out

    def test_whitespace_inside_url(self):
        assert f"{PREFIX}/x.png" in _prefix_css_urls("a{background:url( /x.png )}", PREFIX)

    def test_absolute_and_relative_urls_are_left_alone(self):
        css = "a{background:url(https://cdn.example.com/x.png)}b{background:url(./rel.png)}"
        assert _prefix_css_urls(css, PREFIX) == css

    def test_idempotent(self):
        css = "a{background:url(/bg.png)}"
        once = _prefix_css_urls(css, PREFIX)
        assert _prefix_css_urls(once, PREFIX) == once

    def test_inline_style_block_in_html_is_covered(self):
        html = "<style>body{background:url(/bg.png)}</style>"
        out = _prefix_html_urls(html, PREFIX)
        assert f"url({PREFIX}/bg.png)" in out

    def test_empty_inputs(self):
        assert _prefix_css_urls("", PREFIX) == ""
        assert _prefix_css_urls("a{}", "") == "a{}"
