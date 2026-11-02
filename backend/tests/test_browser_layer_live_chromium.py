"""The browser layer against a real Chromium attached over CDP.

This mirrors production exactly where it matters: Chromium is started *outside*
Playwright (as the sandbox container owns its browser) and Nova attaches with
``connect_over_cdp``. The tools run unmodified; only sandbox resolution is
replaced, by a fake runtime whose thread directories live in tmp_path.
Skips when no Chromium binary is cached.
"""

from __future__ import annotations

import glob
import http.server
import json
import os
import shutil
import socketserver
import subprocess
import tempfile
import threading
import time
from pathlib import Path
from types import SimpleNamespace

import pytest

pytest.importorskip("playwright.sync_api")

from nova.browser import profiles as profiles_mod  # noqa: E402
from nova.browser import session as session_mod  # noqa: E402
from nova.browser import thread_state  # noqa: E402
from nova.config.browser_config import BrowserConfig  # noqa: E402
from nova.tools.builtins import browser_layer_tools as blt  # noqa: E402
from nova.tools.builtins import workspace_tools as wt  # noqa: E402

SECRET = "sess-value-must-not-leak-7731"

PAGES = {
    "/login": f"""<html><head><title>Login</title></head><body><h1>login</h1>
<script>document.cookie = "sid={SECRET}; path=/"; localStorage.setItem("tok", "{SECRET}");</script></body></html>""",
    "/home": """<html><head><title>Home</title></head><body><h1 id=who></h1>
<script>document.getElementById('who').textContent = 'cookie:' + document.cookie + ' ls:' + localStorage.getItem('tok') + ' wd:' + navigator.webdriver + ' tz:' + Intl.DateTimeFormat().resolvedOptions().timeZone;</script></body></html>""",
    "/dl": """<html><body><a id=get href="/file.csv">get</a></body></html>""",
    "/captcha": """<html><head><title>Verify</title></head><body><div class="cf-turnstile" data-sitekey="x"></div>
<iframe src="https://challenges.cloudflare.com/cdn-cgi/challenge-platform/h/b/turnstile/if/ov2/av0" width=300 height=65></iframe></body></html>""",
}


class _Handler(http.server.BaseHTTPRequestHandler):
    def do_GET(self):  # noqa: N802
        if self.path == "/file.csv":
            body = b"a,b\n1,2\n"
            self.send_response(200)
            self.send_header("Content-Type", "text/csv")
            self.send_header("Content-Disposition", 'attachment; filename="report.csv"')
        elif self.path in PAGES:
            body = PAGES[self.path].encode()
            self.send_response(200)
            self.send_header("Content-Type", "text/html")
        else:
            body = b"nope"
            self.send_response(404)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *a):
        pass


@pytest.fixture(scope="module")
def site():
    srv = socketserver.ThreadingTCPServer(("127.0.0.1", 0), _Handler)
    srv.daemon_threads = True
    t = threading.Thread(target=srv.serve_forever, daemon=True)
    t.start()
    yield f"http://127.0.0.1:{srv.server_address[1]}"
    srv.shutdown()


@pytest.fixture(scope="module")
def cdp_url():
    binaries = sorted(glob.glob(os.path.expanduser("~/.cache/ms-playwright/chromium-*/chrome-linux64/chrome")))
    if not binaries:
        pytest.skip("no Chromium available")
    profile = tempfile.mkdtemp(prefix="nova-cdp-")
    proc = subprocess.Popen(
        [binaries[-1], "--headless=new", "--remote-debugging-port=0", f"--user-data-dir={profile}", "--no-first-run", "--no-default-browser-check", "--no-sandbox", "about:blank"],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    port_file = Path(profile) / "DevToolsActivePort"
    deadline = time.time() + 20
    while time.time() < deadline and not port_file.exists():
        time.sleep(0.1)
    if not port_file.exists():
        proc.kill()
        pytest.skip("Chromium did not expose a CDP port")
    port, path = port_file.read_text().split("\n")[:2]
    yield f"ws://127.0.0.1:{port}{path}"
    proc.kill()
    proc.wait(timeout=10)
    shutil.rmtree(profile, ignore_errors=True)


@pytest.fixture
def env(tmp_path, monkeypatch, cdp_url):
    cfg = BrowserConfig()
    monkeypatch.setattr(session_mod, "get_browser_config", lambda: cfg)
    monkeypatch.setattr(profiles_mod, "get_browser_config", lambda: cfg)
    monkeypatch.setattr(blt, "get_browser_config", lambda: cfg)
    monkeypatch.setattr(profiles_mod, "_base_dir", lambda: tmp_path)
    monkeypatch.delenv(profiles_mod.PROFILE_SECRET_ENV, raising=False)
    import nova.sandbox.browser_check as bc

    monkeypatch.setattr(bc, "_cdp_url_for_gateway", lambda client, sandbox=None: cdp_url)
    tid = f"t{int(time.time() * 1000) % 10_000_000}"
    tdir = tmp_path / "users" / "u1" / "threads" / tid
    outputs = tdir / "user-data" / "outputs"
    outputs.mkdir(parents=True)
    runtime = SimpleNamespace(state={"thread_data": {"workspace_path": str(tdir / "user-data" / "workspace"), "outputs_path": str(outputs)}}, context={"user_id": "u1"})
    client = SimpleNamespace()  # no browser_page → every tool takes the CDP path
    resolver = lambda rt: (client, tid, None)  # noqa: E731
    monkeypatch.setattr(wt, "_aio_client_and_thread", resolver)
    monkeypatch.setattr(blt, "_aio_client_and_thread", resolver)
    original = wt._browser_layer

    def layer(*a, **kw):
        lc = original(*a, **kw)
        lc.browser_outputs_dir = str(outputs)  # the browser and the gateway share this path here
        return lc

    monkeypatch.setattr(wt, "_browser_layer", layer)
    monkeypatch.setattr(blt, "_browser_layer", layer)
    yield SimpleNamespace(cfg=cfg, rt=runtime, tid=tid, tdir=tdir, outputs=outputs, base=tmp_path)
    thread_state.forget(tid)


def _call(tool, rt, **kw):
    return tool.func(runtime=rt, description="test", **kw)


def _clear_cookies(cdp_url):
    from playwright.sync_api import sync_playwright

    with sync_playwright() as pw:
        b = pw.chromium.connect_over_cdp(cdp_url)
        b.contexts[0].clear_cookies()
        b.close()


def test_profile_save_then_auto_load_in_a_new_thread(env, site, cdp_url):
    assert "Opened" in _call(wt.browser_navigate_tool, env.rt, url=f"{site}/login")
    out = _call(blt.browser_profile_save_tool, env.rt, name="local", domains=["127.0.0.1"], make_default=True)
    assert "Saved browser profile 'local'" in out, out
    stored = b"".join(p.read_bytes() for p in (env.base / "users" / "u1" / "browser_profiles").iterdir())
    assert SECRET.encode() not in stored

    _clear_cookies(cdp_url)
    thread_state.forget(env.tid)  # a brand-new thread attaching for the first time
    out = _call(wt.browser_navigate_tool, env.rt, url=f"{site}/home")
    assert f"cookie:sid={SECRET}" in out, out
    assert "profile 'local' loaded" in out


def test_stealth_and_timezone_apply_during_the_op(env, site):
    env.cfg.stealth.enabled = True
    env.cfg.timezone = "Asia/Tokyo"
    out = _call(wt.browser_navigate_tool, env.rt, url=f"{site}/home")
    assert "tz:Asia/Tokyo" in out and "wd:undefined" in out, out


def test_click_download_lands_in_outputs(env, site):
    _call(wt.browser_navigate_tool, env.rt, url=f"{site}/dl")
    out = _call(wt.browser_click_tool, env.rt, selector="#get")
    assert "Download saved:" in out and "report.csv" in out, out
    assert (env.outputs / "downloads" / "report.csv").read_bytes() == b"a,b\n1,2\n"
    assert "report.csv" in _call(blt.browser_downloads_tool, env.rt)


def test_pdf(env, site):
    out = _call(blt.browser_pdf_tool, env.rt, url=f"{site}/home", path="reports/home.pdf")
    assert "Saved PDF: /mnt/user-data/outputs/reports/home.pdf" in out, out
    assert (env.outputs / "reports" / "home.pdf").read_bytes().startswith(b"%PDF")
    assert _call(blt.browser_pdf_tool, env.rt, path="../escape.pdf").startswith("Error:")


def test_captcha_returns_needs_human_and_raises_attention(env, site):
    out = _call(wt.browser_navigate_tool, env.rt, url=f"{site}/captcha")
    assert out.startswith("NEEDS_HUMAN"), out
    payload = json.loads(out.split("\n", 1)[0][len("NEEDS_HUMAN ") :])
    assert payload["kind"] == "turnstile"
    att = thread_state.read_attention(env.tdir)
    assert att and att["kind"] == "turnstile"
    thread_state.resolve_attention(env.tdir)
    done = json.loads(_call(blt.browser_await_human_tool, env.rt, timeout_s=10))
    assert done["status"] == "continue" and done["how"] == "user_done"
    assert thread_state.read_attention(env.tdir) is None


def test_recording_writes_frames_and_a_secret_free_manifest(env, site):
    thread_state.update_settings(env.tdir, recording=True)
    _call(wt.browser_navigate_tool, env.rt, url=f"{site}/home?token=abc123&q=ok")
    _call(wt.browser_eval_tool, env.rt, script="document.title + '" + SECRET + "'")
    from nova.browser.recording import list_sessions, read_manifest

    sessions = list_sessions(env.outputs)
    assert len(sessions) == 1
    m = read_manifest(env.outputs, sessions[0]["session_id"])
    assert m["frame_count"] >= 2 and m["action_count"] == 2
    blob = json.dumps(m)
    assert "abc123" not in blob and SECRET not in blob
    assert m["actions"][1]["script_chars"] > 0
    assert (env.outputs / ".recordings" / m["session_id"] / m["frames"][0]["file"]).read_bytes()[:2] == b"\xff\xd8"
