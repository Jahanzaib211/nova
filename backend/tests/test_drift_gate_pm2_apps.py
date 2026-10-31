"""drift-gate's pm2_apps check and the shared scripts/stack_config.py reader.

2026-10-29: nova-litellm was commented out of ecosystem.config.js (its venv was
lost with the disk wipe), but the check's ``name: "..."`` regex still matched
inside the ``/* ... */`` block and reported it "not registered" — RED for an
app that was switched off on purpose.

    cd backend && PYTHONPATH=../scripts uv run pytest tests/test_drift_gate_pm2_apps.py -v
"""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

_REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(_REPO / "scripts"))

import stack_config  # noqa: E402

spec = importlib.util.spec_from_file_location("drift_gate", _REPO / "scripts" / "gates" / "drift-gate.py")
assert spec and spec.loader
drift = importlib.util.module_from_spec(spec)
spec.loader.exec_module(drift)

ECOSYSTEM = """// header: name: "header-comment-app"
module.exports = {
  apps: [
    { name: "nova", script: `${require("path").resolve(__dirname, "x.sh")}` },
    /* TEMPORARILY DISABLED — don't count this
    {
      name: "nova-litellm",
      script: "y.sh",
    },
    */
    {
      name: 'nova-healthcheck', // trailing comment: name: "trailing-app"
      env: {
        URL: "http://example.invalid/a//b",  // the // inside the string stays
        HEALTHCHECK_DISABLED_PROBES:
          "P9_bridge,P10_litellm",
      },
    },
  ],
};
"""


def test_strip_js_comments_keeps_strings_and_drops_comments():
    out = stack_config.strip_js_comments(ECOSYSTEM)
    assert "nova-litellm" not in out
    assert "header-comment-app" not in out and "trailing-app" not in out
    assert '"http://example.invalid/a//b"' in out
    # Block comments keep their newlines so line numbers still match.
    assert out.count("\n") == ECOSYSTEM.count("\n")


def test_declared_apps_ignore_commented_out_blocks():
    out = stack_config.strip_js_comments(ECOSYSTEM)
    assert stack_config.declared_pm2_apps(out) == ["nova", "nova-healthcheck"]


def test_disabled_probes_env_wins_over_ecosystem():
    src = stack_config.strip_js_comments(ECOSYSTEM)
    assert stack_config.disabled_probes(env={}, stripped_src=src) == {"P9_bridge", "P10_litellm"}
    assert stack_config.disabled_probes(env={"HEALTHCHECK_DISABLED_PROBES": "P1_nginx"}, stripped_src=src) == {"P1_nginx"}
    assert stack_config.disabled_probes(env={}, stripped_src="") == set()


def test_real_ecosystem_file_parses():
    src = stack_config.read_ecosystem(_REPO / "ecosystem.config.js")
    assert src is not None
    assert "nova" in stack_config.declared_pm2_apps(src)


def _pm2(apps: dict[str, str]) -> str:
    return json.dumps([{"name": n, "pm2_env": {"status": st, "restart_time": 0, "pm_uptime": 0}} for n, st in apps.items()])


def test_pm2_apps_check_does_not_flag_commented_out_app(monkeypatch, tmp_path):
    root = tmp_path
    (root / "ecosystem.config.js").write_text(ECOSYSTEM, encoding="utf-8")
    monkeypatch.setattr(drift, "REPO_ROOT", root)
    # stack_config lives in the real scripts/ dir; REPO_ROOT only moves the file.
    monkeypatch.setattr(drift, "_stack_config", lambda: stack_config)
    monkeypatch.setattr(drift, "_run", lambda cmd, timeout=60: (0, _pm2({"nova": "online", "nova-healthcheck": "online"}), ""))
    r = drift.check_pm2_apps()
    assert r["status"] == "green", r["detail"]
    assert "nova-litellm" not in r["detail"]


def test_pm2_apps_check_still_flags_a_declared_app_that_is_missing(monkeypatch, tmp_path):
    (tmp_path / "ecosystem.config.js").write_text(ECOSYSTEM, encoding="utf-8")
    monkeypatch.setattr(drift, "REPO_ROOT", tmp_path)
    monkeypatch.setattr(drift, "_stack_config", lambda: stack_config)
    monkeypatch.setattr(drift, "_run", lambda cmd, timeout=60: (0, _pm2({"nova": "online"}), ""))
    r = drift.check_pm2_apps()
    assert r["status"] == "red"
    assert "nova-healthcheck" in r["detail"]
