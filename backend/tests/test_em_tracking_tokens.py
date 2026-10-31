"""HMAC tokens for open/click/unsubscribe links: unforgeable, URL-safe, and the
click redirect only follows a URL whose own HMAC matches (no open redirect)."""

from __future__ import annotations

import pytest

from deerflow.email_marketing.tracking import (
    TrackingLinks,
    make_token,
    sign_url,
    verify_token,
    verify_url,
)

SECRET = "s3cr3t"


def test_token_round_trip_and_tamper_detection():
    tok = make_token(SECRET, "open", "send-1")
    assert verify_token(SECRET, "open", tok) == "send-1"
    assert verify_token(SECRET, "click", tok) is None  # bound to its purpose
    assert verify_token("other", "open", tok) is None
    assert verify_token(SECRET, "open", tok[:-2] + "zz") is None
    assert verify_token(SECRET, "open", "garbage") is None
    assert "/" not in tok and "+" not in tok and "=" not in tok


def test_url_signature_blocks_open_redirect():
    sig = sign_url(SECRET, "send-1", "https://example.com/a?b=1")
    assert verify_url(SECRET, "send-1", "https://example.com/a?b=1", sig)
    assert not verify_url(SECRET, "send-1", "https://evil.example/", sig)
    assert not verify_url(SECRET, "send-2", "https://example.com/a?b=1", sig)


def test_links_are_absolute_and_carry_tokens():
    links = TrackingLinks(base_url="https://nova.example.com/", secret=SECRET, send_id="send-1")
    assert links.open_pixel_url().startswith("https://nova.example.com/api/em/t/o/")
    assert links.open_pixel_url().endswith(".gif")
    click = links.click_url("https://shop.example/?x=1&y=2")
    assert click.startswith("https://nova.example.com/api/em/t/c/")
    assert "u=https%3A%2F%2Fshop.example%2F%3Fx%3D1%26y%3D2" in click
    assert "sig=" in click
    assert links.unsubscribe_url().startswith("https://nova.example.com/api/em/u/")


@pytest.mark.parametrize("purpose", ["open", "click", "unsub"])
def test_every_purpose_is_distinct(purpose):
    tok = make_token(SECRET, purpose, "s")
    others = {p for p in ("open", "click", "unsub") if p != purpose}
    assert all(verify_token(SECRET, o, tok) is None for o in others)
