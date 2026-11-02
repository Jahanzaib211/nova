"""Tracking and unsubscribe links.

Every link Nova puts in a campaign carries an HMAC token bound to one send
and one purpose (``open``, ``click``, ``unsub``); the token is the only
credential the public endpoints accept. Click links additionally carry an
HMAC over the destination URL so ``/t/c`` can never be turned into an open
redirect: a foreign URL with a valid token fails the URL check.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
from dataclasses import dataclass
from urllib.parse import quote

PURPOSES = ("open", "click", "unsub")


def _b64(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")


def _unb64(text: str) -> bytes | None:
    try:
        return base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))
    except (ValueError, TypeError):
        return None


def _mac(secret: str, purpose: str, send_id: str) -> bytes:
    return hmac.new(secret.encode("utf-8"), f"{purpose}\n{send_id}".encode(), hashlib.sha256).digest()[:16]


def make_token(secret: str, purpose: str, send_id: str) -> str:
    if purpose not in PURPOSES:
        raise ValueError(f"unknown purpose {purpose!r}")
    return _b64(send_id.encode("utf-8")) + "." + _b64(_mac(secret, purpose, send_id))


def verify_token(secret: str, purpose: str, token: str) -> str | None:
    """The send id the token names, or None when it is not ours."""
    if not token or "." not in token or purpose not in PURPOSES:
        return None
    sid_part, mac_part = token.split(".", 1)
    sid_raw, mac_raw = _unb64(sid_part), _unb64(mac_part)
    if sid_raw is None or mac_raw is None:
        return None
    try:
        send_id = sid_raw.decode("utf-8")
    except UnicodeDecodeError:
        return None
    if not send_id or not hmac.compare_digest(mac_raw, _mac(secret, purpose, send_id)):
        return None
    return send_id


def sign_url(secret: str, send_id: str, url: str) -> str:
    return _b64(hmac.new(secret.encode("utf-8"), f"url\n{send_id}\n{url}".encode(), hashlib.sha256).digest()[:16])


def verify_url(secret: str, send_id: str, url: str, sig: str) -> bool:
    raw = _unb64(sig or "")
    if raw is None:
        return False
    expected = hmac.new(secret.encode("utf-8"), f"url\n{send_id}\n{url}".encode(), hashlib.sha256).digest()[:16]
    return hmac.compare_digest(raw, expected)


@dataclass(frozen=True, slots=True)
class TrackingLinks:
    base_url: str
    secret: str
    send_id: str

    @property
    def _root(self) -> str:
        return self.base_url.rstrip("/")

    def open_pixel_url(self) -> str:
        return f"{self._root}/api/em/t/o/{make_token(self.secret, 'open', self.send_id)}.gif"

    def click_url(self, target: str) -> str:
        tok = make_token(self.secret, "click", self.send_id)
        return f"{self._root}/api/em/t/c/{tok}?u={quote(target, safe='')}&sig={sign_url(self.secret, self.send_id, target)}"

    def unsubscribe_url(self) -> str:
        return f"{self._root}/api/em/u/{make_token(self.secret, 'unsub', self.send_id)}"

    def unsubscribe_mailto(self, mailbox_domain: str) -> str:
        return f"mailto:unsubscribe+{self.send_id}@{mailbox_domain}?subject=unsubscribe"
