"""``nova.X`` and ``deerflow.X`` must be the same module, not two copies.

The harness keeps module-level singletons -- the sandbox provider cache, the
tool registry -- so if the two import paths produced separate module objects,
code reached through one name would silently not see state created through the
other. That failure is close to undiagnosable from a stack trace, which is why
it is asserted here rather than left to review.
"""

from __future__ import annotations

import importlib

import pytest

ALIASED = [
    "tools",
    "config",
    "config.sandbox_config",
    "sandbox.sandbox_provider",
    "community.aio_sandbox.aio_sandbox_provider",
]


@pytest.mark.parametrize("suffix", ALIASED)
def test_alias_resolves_to_the_same_module(suffix: str) -> None:
    via_nova = importlib.import_module(f"nova.{suffix}")
    via_deerflow = importlib.import_module(f"deerflow.{suffix}")
    assert via_nova is via_deerflow, (
        f"nova.{suffix} and deerflow.{suffix} are different module objects; "
        "module-level singletons would be duplicated"
    )


def test_alias_does_not_shadow_a_missing_module() -> None:
    # The finder must not invent modules: a bad `nova.` path has to fail the
    # same way a bad `deerflow.` path does, or typos become silent no-ops.
    with pytest.raises(ModuleNotFoundError):
        importlib.import_module("nova.definitely_not_a_module")
