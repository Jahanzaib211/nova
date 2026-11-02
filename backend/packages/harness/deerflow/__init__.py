"""``deerflow`` — deprecated compatibility alias for :mod:`nova`.

Nova was forked from DeerFlow and the harness implementation used to live
under this import name. It now lives under ``nova``; this package keeps every
legacy ``deerflow.X`` import path working for one release:

* ``import deerflow.tools`` returns the identical module object as
  ``import nova.tools``. The finder below aliases into ``sys.modules`` rather
  than re-executing anything, which matters because module-level singletons
  (the sandbox provider cache, the tool registry) must not exist twice.
* ``use: deerflow.x:Y`` paths in an existing ``config.yaml`` keep resolving
  through ``nova.reflection.resolve_variable`` / ``resolve_class``, because
  those go through ``importlib.import_module``.

New code must import ``nova``. Nothing here emits warnings: the live config
still carries legacy ``use:`` paths and warning on every resolve would be noise.
"""

from __future__ import annotations

import importlib
import importlib.abc
import importlib.machinery
import sys
from types import ModuleType

_TARGET = "nova"
_ALIAS = "deerflow"


class _AliasFinder(importlib.abc.MetaPathFinder, importlib.abc.Loader):
    """Resolve ``deerflow.X`` by importing ``nova.X`` and aliasing it."""

    def find_spec(
        self,
        fullname: str,
        path: object = None,
        target: ModuleType | None = None,
    ) -> importlib.machinery.ModuleSpec | None:
        # `deerflow` itself is this real module on disk; only submodules alias.
        if not fullname.startswith(f"{_ALIAS}."):
            return None
        real = importlib.import_module(_TARGET + fullname[len(_ALIAS) :])
        spec = importlib.machinery.ModuleSpec(
            fullname,
            self,
            is_package=hasattr(real, "__path__"),
        )
        # Remember the real spec: the import machinery unconditionally stamps
        # the alias spec onto the module, and exec_module() puts it back.
        spec.loader_state = real.__spec__
        return spec

    def create_module(self, spec: importlib.machinery.ModuleSpec) -> ModuleType:
        module = sys.modules[_TARGET + spec.name[len(_ALIAS) :]]
        # Register under BOTH names so a later `import` of either one short
        # circuits to this same object instead of building a second copy.
        sys.modules[spec.name] = module
        return module

    def exec_module(self, module: ModuleType) -> None:
        # Nothing to execute: the aliased module was already initialised by
        # its real name in find_spec(). Only undo the spec overwrite, so
        # `nova.X.__spec__.name` stays `nova.X` (reload, pickling, resources).
        real_spec = module.__spec__.loader_state if module.__spec__ is not None else None
        if real_spec is not None:
            module.__spec__ = real_spec


# Front of the chain, not the back. Appended, the stdlib PathFinder could
# resolve a submodule by searching an aliased parent's __path__ (which points
# into nova/), building a SECOND module object under the deerflow name --
# exactly the duplicate-singleton failure this shim exists to prevent.
if not any(isinstance(finder, _AliasFinder) for finder in sys.meta_path):
    sys.meta_path.insert(0, _AliasFinder())


def __getattr__(name: str) -> ModuleType:
    # `import deerflow; deerflow.config` -- attribute access on the bare alias
    # package resolves to the same object as `nova.config`.
    if not name.startswith("_"):
        try:
            return importlib.import_module(f"{_ALIAS}.{name}")
        except ModuleNotFoundError as exc:
            if exc.name != f"{_TARGET}.{name}":
                raise
    raise AttributeError(f"module {_ALIAS!r} has no attribute {name!r}")


__all__ = ["_AliasFinder"]
