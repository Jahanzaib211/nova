"""Guardrail: no direct process execution outside the Execution Kernel.

Phase C8 — the entire platform has exactly one execution architecture.
This test scans production source (backend/packages + backend/app) for
direct execution primitives and fails if any appear outside
``deerflow/execution/``.

Allowed:
    - deerflow/execution/kernel.py (subprocess.Popen — the one sanctioned site)
    - deerflow/execution/supervisor.py (asyncio process types, termination)
    - deerflow/execution/adapters/interactive_shell.py (PTY shell spawning — Phase C8)
    - formatting-only helpers (subprocess.list2cmdline) — matched narrowly

Run:
    cd backend && uv run pytest tests/test_execution_guardrails.py -v
"""

from __future__ import annotations

import re
from pathlib import Path

BACKEND_ROOT = Path(__file__).resolve().parents[1]
SCAN_ROOTS = [
    BACKEND_ROOT / "packages" / "harness" / "deerflow",
    BACKEND_ROOT / "app",
]

# The only directory allowed to touch execution primitives.
KERNEL_PREFIX = str(Path("packages", "harness", "deerflow", "execution"))

FORBIDDEN = re.compile(
    r"subprocess\.(run|Popen|call|check_output|check_call)\s*\("
    r"|asyncio\.create_subprocess_(exec|shell)\s*\("
    r"|create_subprocess_(exec|shell)\s*\("
    r"|os\.system\s*\("
    r"|os\.popen\s*\("
    r"|shell\s*=\s*True"
)


def _iter_source_files():
    for root in SCAN_ROOTS:
        yield from root.rglob("*.py")


def test_no_direct_execution_outside_kernel():
    violations: list[str] = []
    for path in _iter_source_files():
        rel = str(path.relative_to(BACKEND_ROOT))
        if "__pycache__" in rel:
            continue
        if KERNEL_PREFIX in rel:
            continue
        text = path.read_text(encoding="utf-8", errors="replace")
        for lineno, line in enumerate(text.splitlines(), start=1):
            stripped = line.strip()
            if stripped.startswith("#"):
                continue
            if FORBIDDEN.search(line):
                violations.append(f"{rel}:{lineno}: {stripped[:120]}")
    assert not violations, "Direct process execution found outside the Execution Kernel (route it through deerflow.execution instead):\n" + "\n".join(violations)


def test_kernel_package_is_the_only_popen_site():
    """Within the kernel package itself, Popen appears only in kernel.py or interactive_shell.py.

    Phase C8: interactive_shell.py is a sanctioned site for PTY shell spawning.
    """
    kernel_dir = BACKEND_ROOT / "packages" / "harness" / "deerflow" / "execution"
    sanctioned_popen_files = {"kernel.py", "interactive_shell.py"}
    offenders = []
    for path in kernel_dir.rglob("*.py"):
        if "__pycache__" in str(path):
            continue
        text = path.read_text(encoding="utf-8")
        if "subprocess.Popen(" in text and path.name not in sanctioned_popen_files:
            offenders.append(path.name)
    assert not offenders, f"Popen in non-sanctioned files: {offenders}"
