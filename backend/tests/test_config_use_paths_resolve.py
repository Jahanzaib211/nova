"""Every ``use:`` path in config.yaml must import.

These are the riskiest strings in the repository: they name a module and symbol
that nothing resolves until the provider is first constructed. A typo, a moved
module, or a package rename does not fail at import or at startup -- it fails
the first time that particular tool or provider is needed, which can be days
later and far from the change that caused it.

This is also the gate that has to be green before the ``deerflow`` package can
be renamed: 19 of these paths name it, and the compiler cannot see any of them.
"""

from __future__ import annotations

import importlib
from pathlib import Path

import pytest
import yaml

REPO_ROOT = Path(__file__).resolve().parents[2]
CONFIG_PATH = REPO_ROOT / "config.yaml"


def _use_paths() -> list[str]:
    if not CONFIG_PATH.exists():
        return []
    raw = yaml.safe_load(CONFIG_PATH.read_text(encoding="utf-8")) or {}

    found: list[str] = []

    def walk(node: object) -> None:
        if isinstance(node, dict):
            for key, value in node.items():
                if key == "use" and isinstance(value, str):
                    found.append(value)
                else:
                    walk(value)
        elif isinstance(node, list):
            for item in node:
                walk(item)

    walk(raw)
    return sorted(set(found))


@pytest.mark.skipif(not CONFIG_PATH.exists(), reason="no config.yaml in this checkout")
def test_config_declares_use_paths() -> None:
    # Guards the guard: an empty list would make the test below pass vacuously.
    assert _use_paths(), "config.yaml declares no `use:` targets — parser broken?"


@pytest.mark.parametrize("dotted", _use_paths())
def test_use_path_resolves(dotted: str) -> None:
    # Import the tool package first, as the running gateway does. Several
    # deerflow modules are only importable once something else in their import
    # cycle has been loaded (see test_sandbox_tools_cold_import_is_fragile);
    # resolving from a bare interpreter would fail for reasons unrelated to
    # whether the path in config.yaml is correct, which is what this checks.
    importlib.import_module("deerflow.tools")

    module_name, _, symbol = dotted.partition(":")
    try:
        module = importlib.import_module(module_name)
    except ImportError as exc:  # pragma: no cover - failure path is the point
        pytest.fail(f"config.yaml `use: {dotted}` — cannot import {module_name}: {exc}")
    if symbol:
        assert hasattr(module, symbol), (
            f"config.yaml `use: {dotted}` — {module_name} has no attribute {symbol!r}"
        )


@pytest.mark.xfail(
    reason=(
        "Known latent import cycle, recorded rather than silently worked around. "
        "deerflow.sandbox.tools imports deerflow.tools.types, which runs "
        "deerflow.tools.__init__ -> tools.tools -> builtins.workspace_tools, "
        "which imports back into the half-built deerflow.sandbox.tools. "
        "Production never hits it because the tool registry is always imported "
        "first. Deferring the import under TYPE_CHECKING does NOT fix it: "
        "langchain's @tool decorator introspects real type hints to build its "
        "schemas, so Runtime has to exist at runtime. The fix is to stop "
        "deerflow.tools.__init__ importing the builtins eagerly."
    ),
    strict=True,
)
def test_sandbox_tools_cold_import_is_fragile() -> None:
    import subprocess
    import sys

    result = subprocess.run(
        [sys.executable, "-c", "import deerflow.sandbox.tools"],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr[-400:]
