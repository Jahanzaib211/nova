"""The absproxy must not show a raw Node error when the port has no listener.

The sandbox's own port gateway answers 500 with a plain-text Node error
(``connect ECONNREFUSED 0.0.0.0:4100``) when nothing is listening on the
requested port. That is the *normal* state while a dev server is still
starting, and the Browser tab loads the absproxy URL in an iframe -- so the
raw error string was rendered in place of the app, reading as "the app is
broken" instead of "the server is not up yet".

Our own connect failure already had a word for it ("Nothing on that port yet",
503). The upstream refusal now gets the same translation.

The helper is pinned directly because it is where the decision lives, and it is
deliberately narrow: a large body that merely mentions the word is an app's
own error page and must be passed through untouched.
"""

from __future__ import annotations

from types import SimpleNamespace

from app.gateway.routers.sandbox import _is_upstream_refused


def _upstream(body: str, status: int = 500):
    return SimpleNamespace(content=body.encode("utf-8"), status_code=status)


class TestIsUpstreamRefused:
    def test_detects_node_econnrefused(self):
        assert _is_upstream_refused(_upstream("connect ECONNREFUSED 0.0.0.0:4100")) is True

    def test_detects_go_style_connection_refused(self):
        assert _is_upstream_refused(_upstream("dial tcp 127.0.0.1:4100: connect: connection refused")) is True

    def test_ignores_an_ordinary_error_page(self):
        assert _is_upstream_refused(_upstream("<html><body>Internal Server Error</body></html>")) is False

    def test_ignores_a_large_body_that_merely_mentions_it(self):
        # An app's own page could contain the word; only short upstream bodies
        # are treated as the gateway's refusal message.
        body = "ECONNREFUSED " + ("x" * 600)
        assert _is_upstream_refused(_upstream(body)) is False

    def test_handles_a_missing_body(self):
        assert _is_upstream_refused(SimpleNamespace(content=b"", status_code=500)) is False
