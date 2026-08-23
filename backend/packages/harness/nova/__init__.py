"""``nova`` — the package identity for this harness.

The implementation still lives under ``deerflow`` and is reached through this
name. That is deliberate and is the first half of the rename, not a shortcut:
a big-bang rewrite would have to touch ~1,175 import statements and ~1,300
dotted string references at once, and 19 of those strings are ``use:`` paths in
config.yaml that are resolved dynamically -- a missed one does not fail at
import or at startup, it fails the first time that provider is constructed.

So both names resolve to the same modules. ``import nova.tools`` and
``import deerflow.tools`` return the identical module object, not two copies:
the finder below aliases into ``sys.modules`` rather than re-executing
anything. That matters because module-level singletons (the sandbox provider
cache, the tool registry) must not exist twice.

To finish the rename later, move the source under ``nova/`` and invert this
shim so ``deerflow`` becomes the deprecated alias. Nothing else has to change
in the same commit, which is the point of doing it this way.
"""

from __future__ import annotations

import importlib
import importlib.abc
import importlib.machinery
import sys
from types import ModuleType

_TARGET = "deerflow"
_ALIAS = "nova"


class _AliasFinder(importlib.abc.MetaPathFinder, importlib.abc.Loader):
    """Resolve ``nova.X`` by importing ``deerflow.X`` and aliasing it."""

    def find_spec(
        self,
        fullname: str,
        path: object = None,
        target: ModuleType | None = None,
    ) -> importlib.machinery.ModuleSpec | None:
        if fullname != _ALIAS and not fullname.startswith(f"{_ALIAS}."):
            return None
        # `nova` itself is a real module on disk; only submodules are aliased.
        if fullname == _ALIAS:
            return None
        return importlib.machinery.ModuleSpec(fullname, self, is_package=True)

    def create_module(self, spec: importlib.machinery.ModuleSpec) -> ModuleType:
        suffix = spec.name[len(_ALIAS) + 1 :]
        module = importlib.import_module(f"{_TARGET}.{suffix}")
        # Register under BOTH names so a later `import` of either one short
        # circuits to this same object instead of building a second copy.
        sys.modules[spec.name] = module
        return module

    def exec_module(self, module: ModuleType) -> None:
        # Nothing to execute: the aliased module was already initialised by
        # its real name in create_module().
        return None


# Front of the chain, not the back. Appended, the stdlib PathFinder resolves
# `nova.config.sandbox_config` first by searching the aliased parent's __path__
# (which points into deerflow/), building a SECOND module object under the nova
# name -- exactly the duplicate-singleton failure this shim exists to prevent.
if not any(isinstance(finder, _AliasFinder) for finder in sys.meta_path):
    sys.meta_path.insert(0, _AliasFinder())

__all__ = ["_AliasFinder"]
