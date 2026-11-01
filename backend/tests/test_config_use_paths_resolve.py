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
EXAMPLE_PATH = REPO_ROOT / "config.example.yaml"

# Both files, deliberately. CI does `cp config.example.yaml config.yaml` before
# running the suite (.github/workflows/backend-unit-tests.yml), so a gate that
# reads only config.yaml validates the example in CI and the real file locally.
# The two diverge: the live config declares 19 `use:` targets, the example 16 --
# and the three it omits include the crawl4ai providers, which are exactly the
# dynamically-resolved kind this exists to catch. Reading both means the union
# is checked wherever it runs.
_CONFIG_PATHS = [CONFIG_PATH, EXAMPLE_PATH]


def _use_paths() -> list[str]:
    raw: dict = {}
    for path in _CONFIG_PATHS:
        if not path.exists():
            continue
        loaded = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        raw[path.name] = loaded

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


@pytest.mark.skipif(
    not (CONFIG_PATH.exists() or EXAMPLE_PATH.exists()),
    reason="no config.yaml or config.example.yaml in this checkout",
)
def test_config_declares_use_paths() -> None:
    # Guards the guard: an empty list would make the test below pass vacuously.
    assert _use_paths(), "no `use:` targets found in either config — parser broken?"


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
        assert hasattr(module, symbol), f"config.yaml `use: {dotted}` — {module_name} has no attribute {symbol!r}"


def test_sandbox_tools_cold_import_works() -> None:
    # Was a known latent import cycle, recorded as xfail(strict) so it could
    # not be silently "fixed" by accident:
    #   deerflow.sandbox.tools -> deerflow.tools.types -> deerflow.tools.__init__
    #   -> tools.tools -> builtins.workspace_tools -> deerflow.sandbox.tools
    #   (a cycle back into a half-built module).
    #
    # Fixed by making `deerflow.tools.__init__` lazy: both exports resolve
    # through module __getattr__, so importing a submodule no longer pulls
    # the builtins in eagerly. Pinned by tests/test_import_hygiene.py.
    #
    # The marker was removed when the fix landed; this now asserts the
    # cold import SUCCEEDS, so a regression that re-introduces the cycle
    # fails loudly instead of quietly becoming an xfail again.
    import subprocess
    import sys

    result = subprocess.run(
        [sys.executable, "-c", "import deerflow.sandbox.tools"],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr[-400:]
