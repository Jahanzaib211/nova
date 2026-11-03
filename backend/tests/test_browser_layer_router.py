"""`/api/browser/profiles` and `/api/threads/{id}/browser/*`: owner-scoped, secret-free, traversal-safe."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from uuid import uuid4

import pytest
from _router_auth_helpers import make_authed_test_app
from fastapi.testclient import TestClient

import app.gateway.routers.browser_layer as router_mod
from app.gateway.auth.models import User
from nova.browser import profiles as P
from nova.browser import thread_state
from nova.browser.recording import Recorder
from nova.config.browser_config import BrowserRecordingConfig

SECRET = "cookie-value-xyz-123"


def _user(uid):
    return User(id=uid, email=f"{uid.hex[:6]}@example.com", password_hash="x", system_role="user", created_at=datetime.now(UTC))


@pytest.fixture
def base(tmp_path, monkeypatch):
    monkeypatch.delenv(P.PROFILE_SECRET_ENV, raising=False)
    monkeypatch.setattr(P, "_base_dir", lambda: tmp_path)
    return tmp_path


def _client(user, *, owner=True):
    app = make_authed_test_app(user_factory=lambda: user, owner_check_passes=owner)
    app.include_router(router_mod.router)
    return TestClient(app)


def _save(user, name="work"):
    state = {"cookies": [{"name": "sid", "value": SECRET, "domain": ".example.com", "path": "/"}], "origins": []}
    P.ProfileStore(str(user.id)).save(name, state, ["example.com"])


class TestProfiles:
    def test_list_is_owner_scoped_and_secret_free(self, base):
        alice, bob = _user(uuid4()), _user(uuid4())
        _save(alice)
        body = _client(alice).get("/api/browser/profiles").json()
        assert [p["name"] for p in body["profiles"]] == ["work"]
        assert body["profiles"][0]["domains"] == ["example.com"]
        assert SECRET not in json.dumps(body)
        assert _client(bob).get("/api/browser/profiles").json()["profiles"] == []

    def test_delete_only_own(self, base):
        alice, bob = _user(uuid4()), _user(uuid4())
        _save(alice)
        assert _client(bob).delete("/api/browser/profiles/work").status_code == 404
        assert _client(alice).delete("/api/browser/profiles/work").status_code == 200
        assert _client(alice).get("/api/browser/profiles").json()["profiles"] == []

    def test_default_and_bad_names(self, base):
        alice = _user(uuid4())
        _save(alice)
        c = _client(alice)
        assert c.post("/api/browser/profiles/work/default").status_code == 200
        assert c.get("/api/browser/profiles").json()["profiles"][0]["is_default"] is True
        assert c.delete("/api/browser/profiles-default").status_code == 200
        assert c.post("/api/browser/profiles/missing/default").status_code == 404
        assert c.delete("/api/browser/profiles/..%2Fx").status_code in (400, 404)


@pytest.fixture
def thread(tmp_path, monkeypatch):
    tdir = tmp_path / "threads" / "t1"
    outputs = tdir / "user-data" / "outputs"
    outputs.mkdir(parents=True)
    monkeypatch.setattr(router_mod, "_dirs", lambda tid: (tdir, outputs))
    return tdir, outputs


class TestThreadEndpoints:
    def test_owner_check_blocks_other_users(self, thread):
        c = _client(_user(uuid4()), owner=False)
        assert c.get("/api/threads/t1/browser/state").status_code in (403, 404)
        assert c.get("/api/threads/t1/browser/recordings").status_code in (403, 404)

    def test_state_toggle_and_attention(self, thread):
        tdir, _ = thread
        c = _client(_user(uuid4()))
        assert c.get("/api/threads/t1/browser/state").json()["recording"] is False
        assert c.put("/api/threads/t1/browser/recording", json={"enabled": True}).json()["recording"] is True
        assert thread_state.read_settings(tdir)["recording"] is True
        assert c.post("/api/threads/t1/browser/attention/resolve").status_code == 404
        thread_state.raise_attention(tdir, kind="recaptcha", url="https://x.test/login?token=abc", title="Login")
        att = c.get("/api/threads/t1/browser/state").json()["attention"]
        assert att["kind"] == "recaptcha" and "abc" not in att["url"]
        assert c.post("/api/threads/t1/browser/attention/resolve").status_code == 200
        assert thread_state.read_attention(tdir)["resolved_at"]

    def test_recordings_list_manifest_frame_and_traversal(self, thread):
        _, outputs = thread
        rec = Recorder(outputs, BrowserRecordingConfig())
        rec.add_frame(b"\xff\xd8jpegdata", force=True)
        rec.finish(_FakePage(), "browser_navigate", {"url": "https://x.test/?password=p"}, ok=True)
        c = _client(_user(uuid4()))
        rows = c.get("/api/threads/t1/browser/recordings").json()["recordings"]
        sid = rows[0]["session_id"]
        m = c.get(f"/api/threads/t1/browser/recordings/{sid}").json()
        assert m["actions"][0]["tool"] == "browser_navigate" and "password=p" not in json.dumps(m)
        frame = c.get(f"/api/threads/t1/browser/recordings/{sid}/frames/{m['frames'][0]['file']}")
        assert frame.status_code == 200 and frame.headers["content-type"] == "image/jpeg"
        assert c.get(f"/api/threads/t1/browser/recordings/{sid}/frames/..%2Fmanifest.json").status_code in (400, 404)
        assert c.get("/api/threads/t1/browser/recordings/..%2F..%2Fetc").status_code in (400, 404)
        assert c.get(f"/api/threads/t1/browser/recordings/{sid}/frames/f000001.png").status_code == 400
        assert c.delete(f"/api/threads/t1/browser/recordings/{sid}").status_code == 200
        assert c.get("/api/threads/t1/browser/recordings").json()["recordings"] == []


class _FakePage:
    url = "https://x.test/"
