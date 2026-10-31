"""The sandbox proxies must only ask upstream for encodings they can decode.

Each proxy reads ``upstream.content`` (which httpx decodes) and drops
``Content-Encoding`` before answering. The browser's own ``Accept-Encoding``
used to be forwarded verbatim, so the sandbox answered ``br`` -- and the
gateway has no brotli decoder, so the compressed bytes went out labelled
``text/html`` and the Browser tab rendered them as garbage. Measured against a
live sandbox: ``gzip, deflate, br, zstd`` came back ``content-encoding: br``
with a brotli body; ``gzip, deflate`` came back as the real page.
"""

from __future__ import annotations

import inspect

import httpx
from starlette.requests import Request

from app.gateway.routers import sandbox as sandbox_router
from app.gateway.routers.sandbox import _forward_headers


def _request(headers: dict[str, str]) -> Request:
    raw = [(k.lower().encode(), v.encode()) for k, v in headers.items()]
    return Request({"type": "http", "method": "GET", "path": "/", "headers": raw, "query_string": b""})


def test_browser_accept_encoding_is_replaced_with_decodable_ones():
    fwd = _forward_headers(_request({"Accept-Encoding": "gzip, deflate, br, zstd", "Accept": "text/html"}))
    assert fwd["accept-encoding"] == "gzip, deflate"
    assert fwd["accept"] == "text/html"


def test_every_offered_encoding_is_one_httpx_can_decode():
    # Whatever the proxy offers upstream must round-trip through httpx's own
    # decoder, or the body reaches the browser still compressed.
    offered = [e.strip() for e in _forward_headers(_request({}))["accept-encoding"].split(",")]
    for encoding in offered:
        body = httpx.Response(200, headers={"content-encoding": encoding}, content=_compress(encoding, b"<html>ok</html>"))
        assert body.content == b"<html>ok</html>", encoding


def test_credentials_and_hop_by_hop_headers_are_not_forwarded():
    fwd = _forward_headers(_request({"Cookie": "access_token=x", "Authorization": "Bearer y", "X-CSRF-Token": "z", "Connection": "keep-alive", "Host": "nova", "User-Agent": "ua"}))
    assert set(fwd) == {"user-agent", "accept-encoding"}


def test_no_proxy_forwards_request_headers_by_hand():
    # All three proxies (preview, absproxy, appview) carried their own copy of
    # the header filter; the bug lived in all three. One helper, used by each.
    source = inspect.getsource(sandbox_router)
    assert source.count("request.headers.items()") == 1, "only _forward_headers may copy request headers"
    assert source.count("_forward_headers(request)") == 3


def _compress(encoding: str, data: bytes) -> bytes:
    import gzip
    import zlib

    if encoding == "gzip":
        return gzip.compress(data)
    if encoding == "deflate":
        return zlib.compress(data)
    raise AssertionError(f"unexpected encoding {encoding!r}")
