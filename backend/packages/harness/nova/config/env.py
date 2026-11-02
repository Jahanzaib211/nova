"""Environment-variable names with the legacy DeerFlow fallback.

Nova was forked from DeerFlow and its settings used to be read as
``DEER_FLOW_<X>`` (and, for a few subsystems, ``DEERFLOW_<X>``). The canonical
name is now ``NOVA_<X>``. For one release every read goes through
:func:`getenv_compat`, which returns the first of

    NOVA_<X>  ->  DEER_FLOW_<X>  ->  DEERFLOW_<X>

that is set to a non-empty value, so an existing ``.env``, compose file or
container keeps working with no edit. This is the ONE helper for that rule --
do not hand-roll ``os.getenv("NOVA_X") or os.getenv("DEER_FLOW_X")`` pairs.
"""

from __future__ import annotations

import os
from collections.abc import Mapping

CANONICAL_PREFIX = "NOVA_"
LEGACY_PREFIXES: tuple[str, ...] = ("DEER_FLOW_", "DEERFLOW_")


def env_names(suffix: str) -> tuple[str, ...]:
    """All accepted names for setting *suffix*, canonical first."""
    return (CANONICAL_PREFIX + suffix, *(prefix + suffix for prefix in LEGACY_PREFIXES))


def getenv_compat(suffix: str, default: str | None = None, *, environ: Mapping[str, str] | None = None) -> str | None:
    """Read ``NOVA_<suffix>``, falling back to the legacy DeerFlow names.

    The first name with a non-empty value wins. A variable that is set but
    empty is not skipped silently in favour of the default: if every set name
    is empty, ``""`` is returned (matching ``os.getenv`` for a single name);
    only when none is set at all is *default* returned.
    """
    env = os.environ if environ is None else environ
    saw_empty = False
    for name in env_names(suffix):
        value = env.get(name)
        if value is None:
            continue
        if value != "":
            return value
        saw_empty = True
    return "" if saw_empty else default


def is_set_compat(suffix: str, *, environ: Mapping[str, str] | None = None) -> bool:
    """True when any accepted name for *suffix* is present in the environment."""
    env = os.environ if environ is None else environ
    return any(name in env for name in env_names(suffix))


def env_name_in_use(suffix: str, *, environ: Mapping[str, str] | None = None) -> str:
    """The name :func:`getenv_compat` would read from, for error messages.

    Falls back to the canonical name when nothing is set.
    """
    env = os.environ if environ is None else environ
    for name in env_names(suffix):
        if env.get(name):
            return name
    return CANONICAL_PREFIX + suffix


__all__ = ["CANONICAL_PREFIX", "LEGACY_PREFIXES", "env_name_in_use", "env_names", "getenv_compat", "is_set_compat"]
