"""The regression gate's HTTP probe identifies itself.

On 2026-09-21 the gate reported ``public: 403`` for
https://nova.alilabsx.com/health while the site was perfectly healthy — curl
got 200 from the same host at the same moment. urllib defaults to a
``Python-urllib/3.x`` User-Agent, which Cloudflare blocks outright, so the
gate was reporting production down when it was up. A gate that cries wolf is
worse than no gate: it is the fastest way to teach everyone to ignore the
board, which is how the sandbox image vanished unnoticed.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path

SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "gates" / "regression-gate.py"
spec = importlib.util.spec_from_file_location("regression_gate", SCRIPT)
assert spec and spec.loader
gate = importlib.util.module_from_spec(spec)
spec.loader.exec_module(gate)


def test_probe_sends_a_descriptive_user_agent(monkeypatch):
    seen: dict = {}

    class _Resp:
        status = 200

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

    def fake_opener(*_args, **_kwargs):
        class _Opener:
            def open(self, req, timeout=None):
                seen["headers"] = dict(req.headers)
                return _Resp()

        return _Opener()

    monkeypatch.setattr(gate.urllib.request, "build_opener", fake_opener)

    assert gate.http_status("https://example.test/health") == 200
    ua = seen["headers"].get("User-agent") or seen["headers"].get("User-Agent")
    assert ua, "probe sent no User-Agent — Cloudflare answers those with 403"
    assert "python-urllib" not in ua.lower(), "the blocked default is back"
    assert "Nova" in ua


def test_explicit_headers_still_win(monkeypatch):
    """Callers that pass auth headers must not lose them to the default."""
    seen: dict = {}

    class _Resp:
        status = 204

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

    def fake_opener(*_args, **_kwargs):
        class _Opener:
            def open(self, req, timeout=None):
                seen["headers"] = dict(req.headers)
                return _Resp()

        return _Opener()

    monkeypatch.setattr(gate.urllib.request, "build_opener", fake_opener)

    gate.http_status("https://example.test/x", headers={"Authorization": "Bearer t"})
    assert seen["headers"].get("Authorization") == "Bearer t"
    assert any("nova" in str(v).lower() for v in seen["headers"].values())
