"""Sandbox content gets `allow-same-origin` only when our own shell frames it.

Two CSPs exist for proxied sandbox content:

* `_FRAMED_SANDBOX_CSP` grants `allow-same-origin`, which framed content needs —
  without it sub-resource requests arrive cookie-less, the auth middleware 401s
  every asset, and `localStorage` / `document.cookie` throw during hydration.
* `_OPAQUE_SANDBOX_CSP` withholds it, because the generic absproxy fronts
  *arbitrary in-container ports* and handing every one of them the user's
  gateway session is a much wider blast radius than the preview proxy, which is
  scoped to one registered dev-server port.

The absproxy was pinned to the opaque one on the stated grounds that it "is not
framed by the app shell". It is: `buildPreviewSrc` in `browser-tab.tsx` returns
the absproxy URL as the iframe `src` whenever the canonical preview proxy cannot
reach the dev server. So a framed absproxy hit every failure above — the page
server-rendered and then failed to hydrate, which is "renders correctly in
Chrome, wrong in Nova's Browser tab".

Selection is now per-request on `Sec-Fetch-Dest`, which is a forbidden header
name: a page cannot forge it from `fetch`/XHR, only the browser emits it. That
is only trustworthy because `frame-ancestors` now restricts who may frame these
responses at all — previously unset, so any site could embed them.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from app.gateway.routers.sandbox import (
    _FRAMED_SANDBOX_CSP,
    _OPAQUE_SANDBOX_CSP,
    _is_framed_request,
    _sandbox_csp,
)


def _request(**headers):
    return SimpleNamespace(headers={k.lower(): v for k, v in headers.items()})


class TestFramedDetection:
    def test_an_iframe_load_is_framed(self):
        assert _is_framed_request(_request(**{"Sec-Fetch-Dest": "iframe"})) is True

    @pytest.mark.parametrize(
        "dest",
        ["document", "empty", "script", "image", "object", "embed"],
        ids=lambda d: d,
    )
    def test_every_other_destination_is_not(self, dest):
        assert _is_framed_request(_request(**{"Sec-Fetch-Dest": dest})) is False

    def test_a_missing_header_is_not_framed(self):
        """curl, a server-side fetch, an old browser — all get the safe default."""
        assert _is_framed_request(_request()) is False

    def test_the_header_is_matched_case_insensitively(self):
        assert _is_framed_request(_request(**{"Sec-Fetch-Dest": "IFRAME"})) is True


class TestCspSelection:
    def test_framed_content_gets_allow_same_origin(self):
        assert "allow-same-origin" in _sandbox_csp(framed=True)

    def test_unframed_content_does_not(self):
        csp = _sandbox_csp(framed=False)
        assert "allow-same-origin" not in csp, "an arbitrary in-container port must not carry the user's session"

    def test_both_start_from_the_declared_constants(self):
        assert _sandbox_csp(framed=True).startswith(_FRAMED_SANDBOX_CSP)
        assert _sandbox_csp(framed=False).startswith(_OPAQUE_SANDBOX_CSP)

    def test_the_sandbox_directive_survives_in_both(self):
        for framed in (True, False):
            csp = _sandbox_csp(framed=framed)
            assert csp.startswith("sandbox "), csp
            assert "allow-scripts" in csp


class TestFrameAncestors:
    """Unset before this change, so any site could frame sandbox content — which
    matters precisely because the framed CSP grants the gateway origin."""

    def test_both_csps_restrict_who_may_frame_them(self):
        for framed in (True, False):
            assert "frame-ancestors" in _sandbox_csp(framed=framed)

    def test_self_is_always_allowed(self):
        assert "'self'" in _sandbox_csp(framed=True)

    def test_configured_split_origins_are_allowed(self, monkeypatch):
        """`'self'` alone would blank every preview on a split-origin deployment."""
        import app.gateway.routers.sandbox as sandbox_router

        monkeypatch.setattr(
            sandbox_router,
            "get_configured_cors_origins",
            lambda: {"https://nova.example.com"},
        )
        csp = _sandbox_csp(framed=True)
        assert "'self' https://nova.example.com" in csp

    def test_it_reuses_the_cors_allowlist_rather_than_its_own(self):
        """One source of truth: the same origins allowed to call the API are the
        ones allowed to frame sandbox content."""
        import app.gateway.routers.sandbox as sandbox_router

        assert hasattr(sandbox_router, "get_configured_cors_origins")

    def test_the_directive_is_separated_correctly(self):
        csp = _sandbox_csp(framed=False)
        assert "; frame-ancestors " in csp, "CSP directives are semicolon-separated"
