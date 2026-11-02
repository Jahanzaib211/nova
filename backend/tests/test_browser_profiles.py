"""Browser profiles: encrypted at rest, domain-scoped, owner-bucketed."""

from __future__ import annotations

import json
import os
import time

import pytest

from nova.browser import profiles as P
from nova.config.browser_config import BrowserProfilesConfig

SECRET_VALUE = "s3cr3t-session-value-do-not-leak"


def _state():
    return {
        "cookies": [
            {"name": "sid", "value": SECRET_VALUE, "domain": ".example.com", "path": "/", "expires": -1, "httpOnly": True, "secure": True, "sameSite": "Lax"},
            {"name": "pref", "value": "dark", "domain": "app.example.com", "path": "/settings", "expires": -1, "httpOnly": False, "secure": False, "sameSite": "Lax"},
            {"name": "trk", "value": "x", "domain": ".tracker.net", "path": "/", "expires": -1, "httpOnly": False, "secure": False, "sameSite": "None"},
            {"name": "old", "value": "y", "domain": ".example.com", "path": "/", "expires": 1.0, "httpOnly": False, "secure": False, "sameSite": "Lax"},
        ],
        "origins": [
            {"origin": "https://app.example.com", "localStorage": [{"name": "token", "value": SECRET_VALUE}]},
            {"origin": "https://tracker.net", "localStorage": [{"name": "a", "value": "b"}]},
        ],
    }


@pytest.fixture
def store(tmp_path, monkeypatch):
    monkeypatch.delenv(P.PROFILE_SECRET_ENV, raising=False)
    return P.ProfileStore("user-1", base_dir=tmp_path, config=BrowserProfilesConfig())


class TestDomainLogic:
    def test_normalize(self):
        assert P.normalize_domain("https://Sub.Example.com:443/x") == "sub.example.com"
        assert P.normalize_domain(".example.com") == "example.com"
        with pytest.raises(P.ProfileError):
            P.normalize_domain("../etc/passwd")

    def test_host_matches_requires_label_boundary(self):
        assert P.host_matches("a.example.com", "example.com")
        assert not P.host_matches("badexample.com", "example.com")

    def test_cookie_applies_to(self):
        sid, pref = _state()["cookies"][:2]
        assert P.cookie_applies_to(sid, "https://app.example.com/x")
        assert not P.cookie_applies_to(sid, "http://app.example.com/x"), "secure cookie must not go over http"
        assert P.cookie_applies_to(pref, "http://app.example.com/settings/a")
        assert not P.cookie_applies_to(pref, "http://app.example.com/other")
        assert not P.cookie_applies_to(sid, "https://evil-example.com/")


class TestStore:
    def test_save_scopes_to_domains_and_encrypts(self, store):
        meta = store.save("work", _state(), ["example.com"])
        assert meta["domains"] == ["example.com"]
        assert meta["cookie_count"] == 3  # tracker.net cookie dropped
        assert meta["origin_count"] == 1
        raw = b"".join(p.read_bytes() for p in store.dir.iterdir())
        assert SECRET_VALUE.encode() not in raw, "cookie/localStorage values must be encrypted at rest"
        assert b"sid" not in (store.dir / "work.state.enc").read_bytes()
        assert oct(os.stat(store.dir / "work.state.enc").st_mode & 0o777) == "0o600"

    def test_round_trip(self, store):
        store.save("work", _state(), ["example.com"])
        prof = store.load("work")
        assert {c["name"] for c in prof.cookies_for("https://app.example.com/")} == {"sid"}  # expired 'old' excluded
        assert prof.cookies_for("https://tracker.net/") == []
        assert prof.origins_for("https://app.example.com/page")[0]["localStorage"][0]["name"] == "token"

    def test_derives_domains_when_not_given(self, store):
        meta = store.save("all", _state())
        assert "tracker.net" in meta["domains"] and "example.com" in meta["domains"]

    def test_list_does_not_decrypt_or_expose_values(self, store):
        store.save("work", _state(), ["example.com"])
        listing = json.dumps(store.list())
        assert SECRET_VALUE not in listing and "work" in listing

    @pytest.mark.parametrize("bad", ["", "../x", "a/b", ".hidden", "x" * 65, "a b"])
    def test_rejects_unsafe_names(self, store, bad):
        with pytest.raises(P.ProfileError):
            store.save(bad, _state(), ["example.com"])

    def test_user_isolation(self, tmp_path, monkeypatch):
        monkeypatch.delenv(P.PROFILE_SECRET_ENV, raising=False)
        a = P.ProfileStore("alice", base_dir=tmp_path)
        b = P.ProfileStore("bob", base_dir=tmp_path)
        a.save("work", _state(), ["example.com"])
        assert b.list() == []
        with pytest.raises(P.ProfileError):
            b.load("work")

    def test_default_and_match(self, store):
        store.save("a", _state(), ["example.com"])
        store.save("b", _state(), ["app.example.com"])
        assert store.match_url("https://app.example.com/").name == "b"  # most specific
        store.set_default("a")
        assert store.default_name() == "a"
        assert store.match_url("https://app.example.com/").name == "a"  # default wins
        assert store.match_url("https://other.org/") is None

    def test_delete(self, store):
        store.save("a", _state(), ["example.com"])
        assert store.delete("a") is True
        assert store.list() == [] and store.delete("a") is False

    def test_caps(self, tmp_path, monkeypatch):
        monkeypatch.delenv(P.PROFILE_SECRET_ENV, raising=False)
        s = P.ProfileStore("u", base_dir=tmp_path, config=BrowserProfilesConfig(max_profiles=1))
        s.save("a", _state(), ["example.com"])
        with pytest.raises(P.ProfileError, match="limit"):
            s.save("b", _state(), ["example.com"])
        s.save("a", _state(), ["example.com"])  # overwrite is allowed
        big = {"cookies": [{"name": "c", "value": "v" * 20_000, "domain": "example.com", "path": "/"}], "origins": []}
        small = P.ProfileStore("v", base_dir=tmp_path, config=BrowserProfilesConfig(max_bytes=10_000))
        with pytest.raises(P.ProfileError, match="cap"):
            small.save("big", big, ["example.com"])

    def test_nothing_in_scope(self, store):
        with pytest.raises(P.ProfileError):
            store.save("x", _state(), ["unrelated.org"])

    def test_rotated_secret_is_a_clean_error(self, store, monkeypatch):
        store.save("a", _state(), ["example.com"])
        monkeypatch.setenv(P.PROFILE_SECRET_ENV, "a-different-secret")
        with pytest.raises(P.ProfileError, match="decrypt"):
            store.load("a")

    def test_cookies_for_fetch_respects_flags(self, tmp_path, monkeypatch):
        monkeypatch.delenv(P.PROFILE_SECRET_ENV, raising=False)
        monkeypatch.setattr(P, "_base_dir", lambda: tmp_path)
        P.ProfileStore("u1", base_dir=tmp_path).save("w", _state(), ["example.com"])
        assert [c["name"] for c in P.cookies_for_fetch("https://example.com/", "u1")] == ["sid"]
        assert P.cookies_for_fetch("https://example.com/", "u1", use_profile=False) == []
        assert P.cookies_for_fetch("https://example.com/", "someone-else") == []
        assert P.cookies_for_fetch("https://example.com/", None) == []

    def test_redact(self):
        assert SECRET_VALUE not in json.dumps(P.redact_cookies(_state()["cookies"]))


def test_expired_check_uses_now():
    c = {"name": "a", "value": "b", "domain": "x.com", "path": "/", "expires": time.time() + 100}
    assert P.cookie_applies_to(c, "http://x.com/")
    assert not P.cookie_applies_to(c, "http://x.com/", now=time.time() + 1000)
