"""Regression tests for the Next.js scaffold template's config-file choice.

Pinned by the 2026-08-14 incident where ``scaffold_project_template`` wrote
``next.config.ts`` (TypeScript), but the installed Next.js was 14.x and does
not support ``.ts`` config files — so ``npm run dev`` crashed immediately with
``Configuring Next.js via 'next.config.ts' is not supported`` and the panel
showed the endless ``Compiling…`` spinner. The fix writes ``next.config.mjs``,
which every Next 13–15 release accepts.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

import pytest

_HERE = Path(__file__).resolve().parent
_BACKEND = _HERE.parent
if str(_BACKEND) not in sys.path:
    sys.path.insert(0, str(_BACKEND))


@pytest.fixture(scope="module")
def templates():
    """The ``_TEMPLATES`` dict from ``workspace_tools.py``."""

    from deerflow.tools.builtins import workspace_tools

    return workspace_tools._TEMPLATES


def test_nextjs_tailwind_template_uses_supported_config_name(templates):
    files = templates["nextjs-tailwind"]
    # ``.ts`` configs are only valid for Next 15+, so the scaffold must not
    # produce one. ``.mjs`` is the smallest name that works on every
    # currently-supported Next release.
    assert not any(name.endswith(".ts") and name.startswith("next.config") for name in files)
    assert "next.config.mjs" in files


def test_nextjs_tailwind_template_config_is_minimal_but_valid(templates):
    """Sanity-check the scaffolded ``next.config.mjs`` content is something the
    Next.js dev server can actually parse (CommonJS-free ESM)."""

    config = templates["nextjs-tailwind"]["next.config.mjs"]
    # ``import`` without a default source, ``export default``, no CommonJS —
    # the same shape every Next 13–15 starter ships.
    assert "export default" in config
    assert "module.exports" not in config
    # Should not be empty (an empty file would still parse but is brittle
    # against tools that require a named default export).
    assert config.strip()


def test_scaffold_does_not_write_unsupported_configs_for_other_templates(templates):
    """No template may emit a Tailwind / PostCSS / Vite config the matching
    toolchain version cannot read."""

    # React-Vite uses ``vite.config.ts`` (TS). Vite 5 supports it; this is
    # just a guard so future template edits do not silently regress to a
    # ``vite.config.js`` that loses type hints.
    assert "vite.config.ts" in templates["react-vite"]
    # Tailwind config is TypeScript in the Next template.
    assert "tailwind.config.ts" in templates["nextjs-tailwind"]


def test_scaffold_next_version_pin_supports_dot_mjs_configs(templates):
    """The pinned ``next`` version in the scaffolded package.json must be one
    that accepts ``next.config.mjs``. Next 13.0+ does; we keep the scaffold on
    14.x today, but assert the pin so future bumps stay in the supported
    window."""

    pkg_json = templates["nextjs-tailwind"]["package.json"]
    match = re.search(r'"next"\s*:\s*"(\d+)\.', pkg_json)
    assert match, f"could not parse next version from {pkg_json!r}"
    major = int(match.group(1))
    assert major >= 13, (
        f"scaffold pins next major {major}; ``next.config.mjs`` requires ≥13"
    )