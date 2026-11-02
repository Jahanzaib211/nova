"""Boundary check: harness layer must not import from app layer.

The nova-harness package (packages/harness/nova/) is a standalone,
publishable agent framework. It must never depend on the app layer (app/).

This test scans all Python files in the harness package and fails if any
``from app.`` or ``import app.`` statement is found.
"""

import ast
from pathlib import Path

BACKEND_ROOT = Path(__file__).parent.parent
HARNESS_ROOT = BACKEND_ROOT / "packages" / "harness" / "nova"
# The legacy compatibility alias ships in the same wheel and obeys the same rule.
LEGACY_ALIAS_ROOT = BACKEND_ROOT / "packages" / "harness" / "deerflow"

BANNED_PREFIXES = ("app.",)


def _collect_imports(filepath: Path) -> list[tuple[int, str]]:
    """Return (line_number, module_path) for every import in *filepath*."""
    source = filepath.read_text(encoding="utf-8")
    try:
        tree = ast.parse(source, filename=str(filepath))
    except SyntaxError:
        return []

    results: list[tuple[int, str]] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                results.append((node.lineno, alias.name))
        elif isinstance(node, ast.ImportFrom):
            if node.module:
                results.append((node.lineno, node.module))
    return results


def test_harness_does_not_import_app():
    violations: list[str] = []

    for py_file in sorted([*HARNESS_ROOT.rglob("*.py"), *LEGACY_ALIAS_ROOT.rglob("*.py")]):
        for lineno, module in _collect_imports(py_file):
            if any(module == prefix.rstrip(".") or module.startswith(prefix) for prefix in BANNED_PREFIXES):
                rel = py_file.relative_to(HARNESS_ROOT.parent.parent.parent)
                violations.append(f"  {rel}:{lineno}  imports {module}")

    assert not violations, "Harness layer must not import from app layer:\n" + "\n".join(violations)


# Alembic revisions are immutable history: they were written against the old
# import name and keep resolving through the compatibility alias.
_LEGACY_IMPORT_ALLOWED = (HARNESS_ROOT / "persistence" / "migrations" / "versions",)


def test_first_party_code_does_not_import_the_legacy_alias():
    """``deerflow`` is a compatibility alias for outside callers only.

    Harness and app code must import ``nova`` directly. Going through the alias
    from inside the package re-enters the import of a half-initialised module
    and is the one way the alias could hand out a second module object.
    """
    violations: list[str] = []
    for root in (HARNESS_ROOT, BACKEND_ROOT / "app"):
        for py_file in sorted(root.rglob("*.py")):
            if any(py_file.is_relative_to(allowed) for allowed in _LEGACY_IMPORT_ALLOWED):
                continue
            for lineno, module in _collect_imports(py_file):
                if module == "deerflow" or module.startswith("deerflow."):
                    rel = py_file.relative_to(BACKEND_ROOT)
                    violations.append(f"  {rel}:{lineno}  imports {module}")

    assert not violations, "Import `nova`, not the legacy `deerflow` alias:\n" + "\n".join(violations)
