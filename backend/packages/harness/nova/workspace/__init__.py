"""Workspace Intelligence Kernel — Nova Phase C9.

Architecture: the Workspace Intelligence Kernel (WIK) is a deterministic
repository understanding engine that provides structured intelligence
to the Nova agent before execution begins.

Package structure::

    workspace/           # Phase C9 — Workspace Intelligence Kernel
    ├── models/         # Frozen domain dataclasses
    ├── scanners/       # Bounded filesystem traversal
    ├── detectors/      # Language-agnostic + per-language detectors
    ├── parsers/        # Per-language AST/content parsers
    ├── graph/         # Graph construction and query
    ├── planner/       # Execution planning
    ├── cache/          # Persistent + in-memory cache
    ├── events/         # Domain events
    └── metrics/        # Observability

Activation modes (staged rollout):

    disabled  — kernel exists but is inactive
    shadow    — builds graph silently, existing path is authoritative
    enabled   — workspace intelligence is the default planner

Search hierarchy::

    1. Workspace Graph
    2. Symbol Index
    3. AST / Language Index
    4. Dependency Graph
    5. Command Registry
    6. Bounded Walker
    7. grep_files  (final fallback, never removed)
"""

from __future__ import annotations

__version__ = "0.1.0"
