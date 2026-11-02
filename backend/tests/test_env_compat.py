"""NOVA_<X> env names with the legacy DEER_FLOW_<X> / DEERFLOW_<X> fallback."""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

from nova.config.env import env_name_in_use, env_names, getenv_compat, is_set_compat


def test_names_are_canonical_first():
    assert env_names("CONFIG_PATH") == ("NOVA_CONFIG_PATH", "DEER_FLOW_CONFIG_PATH", "DEERFLOW_CONFIG_PATH")


@pytest.mark.parametrize(
    ("environ", "expected"),
    [
        ({"NOVA_X": "new", "DEER_FLOW_X": "old"}, "new"),
        ({"DEER_FLOW_X": "old"}, "old"),
        ({"DEERFLOW_X": "older"}, "older"),
        ({"DEER_FLOW_X": "old", "DEERFLOW_X": "older"}, "old"),
        # An empty canonical value does not mask a real legacy one.
        ({"NOVA_X": "", "DEER_FLOW_X": "old"}, "old"),
        # Set-but-empty everywhere reads as "" (like os.getenv), not the default.
        ({"NOVA_X": ""}, ""),
        ({}, "dflt"),
    ],
)
def test_getenv_compat_precedence(environ, expected):
    assert getenv_compat("X", "dflt", environ=environ) == expected


def test_reads_process_environment(monkeypatch):
    monkeypatch.delenv("NOVA_COMPAT_PROBE", raising=False)
    monkeypatch.setenv("DEER_FLOW_COMPAT_PROBE", "legacy")
    assert getenv_compat("COMPAT_PROBE") == "legacy"
    monkeypatch.setenv("NOVA_COMPAT_PROBE", "canonical")
    assert getenv_compat("COMPAT_PROBE") == "canonical"


def test_is_set_and_name_in_use():
    env = {"DEER_FLOW_Y": "1"}
    assert is_set_compat("Y", environ=env)
    assert not is_set_compat("Z", environ=env)
    assert env_name_in_use("Y", environ=env) == "DEER_FLOW_Y"
    assert env_name_in_use("Z", environ=env) == "NOVA_Z"


@pytest.mark.parametrize("suffix", ["CONFIG_PATH", "EXTENSIONS_CONFIG_PATH", "PROJECT_ROOT", "SKILLS_PATH"])
def test_colliding_names_resolve_canonical_first(monkeypatch, suffix):
    """The four names that already existed under both prefixes mean the same thing.

    ``NOVA_CONFIG_PATH`` / ``NOVA_EXTENSIONS_CONFIG_PATH`` / ``NOVA_PROJECT_ROOT``
    / ``NOVA_SKILLS_PATH`` were documented but never read; they are now read
    with the documented meaning (identical to the DEER_FLOW_ twin) and win.
    """
    monkeypatch.setenv(f"DEER_FLOW_{suffix}", "/legacy")
    monkeypatch.setenv(f"NOVA_{suffix}", "/canonical")
    assert getenv_compat(suffix) == "/canonical"


_BACKEND = Path(__file__).resolve().parents[1]
_LEGACY_READ_ALLOWED = {
    # The helper itself names the legacy prefixes.
    _BACKEND / "packages" / "harness" / "nova" / "config" / "env.py",
}


def _direct_legacy_reads(path: Path) -> list[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    hits: list[str] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call) or not node.args:
            continue
        func = node.func
        name = ast.unparse(func)
        if name not in {"os.getenv", "os.environ.get", "getenv", "environ.get"}:
            continue
        first = node.args[0]
        if isinstance(first, ast.Constant) and isinstance(first.value, str) and first.value.startswith(("DEER_FLOW_", "DEERFLOW_")):
            hits.append(f"{path.relative_to(_BACKEND)}:{node.lineno} {name}({first.value!r})")
    return hits


def test_no_direct_legacy_env_reads():
    """Every DEER_FLOW_/DEERFLOW_ read goes through getenv_compat, so NOVA_ wins."""
    hits: list[str] = []
    for root in (_BACKEND / "app", _BACKEND / "packages" / "harness" / "nova"):
        for py in root.rglob("*.py"):
            if py in _LEGACY_READ_ALLOWED or "migrations/versions" in py.as_posix():
                continue
            hits.extend(_direct_legacy_reads(py))
    assert not hits, "read these via nova.config.env.getenv_compat:\n" + "\n".join(hits)
