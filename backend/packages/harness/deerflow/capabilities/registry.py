"""The registry: modules in, deterministic snapshot + invoke out."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable
from typing import Any

from pydantic import BaseModel, ValidationError

from deerflow.capabilities.types import (
    HARNESS_TOOL_KINDS,
    CapabilityModule,
    ModuleStatus,
    OpContext,
    Operation,
    OperationDenied,
    OperationNotFound,
)

logger = logging.getLogger(__name__)

SNAPSHOT_VERSION = 1


def surface_allows(op: Operation, surface: str) -> bool:
    """Whether ``op`` may be reached from ``surface`` at all.

    ``harness`` (a model's tool call) and ``mcp`` (an external harness on a
    token) never reach ``secret`` operations, and the harness never reaches
    ``admin`` ones; each also honours the op's own opt-out. ``api`` is the
    session-authenticated UI and reaches everything its caller may.
    """
    if surface == "harness":
        return op.harness and op.kind in HARNESS_TOOL_KINDS
    if surface == "mcp":
        return op.mcp and op.kind != "secret"
    return True


def _default_flags() -> dict[str, bool]:
    from deerflow.capabilities.modules.features import compute_flags

    return compute_flags()


class CapabilityRegistry:
    """Modules in, snapshot and policy-checked invoke out.

    ``flags`` supplies the live feature flags; it is read on every
    :meth:`invoke`, so an operation behind a switched-off flag is refused
    on every surface, not merely left out of one surface's listing.
    """

    def __init__(self, *, flags: Callable[[], dict[str, bool]] | None = None) -> None:
        self._modules: dict[str, CapabilityModule] = {}
        self._ops: dict[str, Operation] = {}
        self._flags = flags or _default_flags

    # -- registration -----------------------------------------------------

    def register(self, module: CapabilityModule) -> None:
        if module.id in self._modules:
            raise ValueError(f"capability module {module.id!r} already registered")
        for op in module.operations:
            if not op.name.startswith(module.id + "."):
                raise ValueError(f"operation {op.name!r} must start with {module.id + '.'!r}")
            if op.name in self._ops:
                raise ValueError(f"operation {op.name!r} already registered")
            flag = op.flag if op.flag is not None else module.flag
            if flag != op.flag:
                op = Operation(**{**op.__dict__, "flag": flag})
            self._ops[op.name] = op
        self._modules[module.id] = module

    # -- lookup -----------------------------------------------------------

    def modules(self) -> list[CapabilityModule]:
        return [self._modules[k] for k in sorted(self._modules)]

    def get(self, name: str) -> Operation:
        try:
            return self._ops[name]
        except KeyError as exc:
            raise OperationNotFound(name) from exc

    def operations(self, *, enabled_flags: set[str] | None = None) -> list[Operation]:
        """Operations whose flag is on (or unflagged). ``None`` = ignore flags."""
        out = []
        for name in sorted(self._ops):
            op = self._ops[name]
            if enabled_flags is not None and op.flag is not None and op.flag not in enabled_flags:
                continue
            out.append(op)
        return out

    # -- policy -----------------------------------------------------------

    def enabled_flags(self) -> set[str]:
        return {k for k, v in self._flags().items() if v}

    def denial(self, op: Operation, ctx: OpContext, *, enabled_flags: set[str] | None = None) -> str | None:
        """Why ``ctx`` may not invoke ``op``, or ``None`` when it may.

        The one policy every surface applies — the ``/api/capabilities/ops``
        endpoint, lead-agent tools and Nova's MCP server used to each carry
        their own subset of it.
        """
        if op.admin_only and not ctx.is_admin:
            return f"{op.name} is admin-only"
        if not surface_allows(op, ctx.surface):
            return f"{op.name} is not available on the {ctx.surface} surface"
        if op.flag is not None:
            flags = self.enabled_flags() if enabled_flags is None else enabled_flags
            if op.flag not in flags:
                return f"{op.name} is disabled (feature flag {op.flag!r} is off)"
        return None

    def permitted(self, ctx: OpContext, *, enabled_flags: set[str] | None = None) -> list[Operation]:
        """Every operation ``ctx`` may invoke, sorted by name."""
        flags = self.enabled_flags() if enabled_flags is None else enabled_flags
        return [op for op in self.operations() if self.denial(op, ctx, enabled_flags=flags) is None]

    # -- derivations ------------------------------------------------------

    def snapshot(self) -> dict[str, Any]:
        """The contract. Everything, flags ignored, sorted, JSON-serialisable."""
        return {
            "version": SNAPSHOT_VERSION,
            "modules": [
                {
                    "id": m.id,
                    "title": m.title,
                    "flag": m.flag,
                    "config_key": m.config_key,
                    "description": m.description,
                    "operations": sorted(op.name for op in m.operations),
                }
                for m in self.modules()
            ],
            "operations": [
                {
                    "name": op.name,
                    "module": op.module_id,
                    "kind": op.kind,
                    "description": op.description,
                    "flag": op.flag,
                    "harness": op.harness,
                    "mcp": op.mcp,
                    "admin_only": op.admin_only,
                    "input_schema": op.input.model_json_schema(),
                    "output_schema": op.output.model_json_schema(),
                }
                for op in self.operations()
            ],
        }

    async def statuses(self) -> dict[str, ModuleStatus]:
        mods = self.modules()
        results = await asyncio.gather(*(m.status() for m in mods), return_exceptions=True)
        out: dict[str, ModuleStatus] = {}
        for m, r in zip(mods, results, strict=True):
            if isinstance(r, BaseException):
                out[m.id] = ModuleStatus(configured=False, healthy=False, detail=f"status probe failed: {r}")
            else:
                out[m.id] = r
        return out

    async def invoke(self, name: str, ctx: OpContext, payload: dict[str, Any] | BaseModel | None, *, enabled_flags: set[str] | None = None) -> dict[str, Any]:
        op = self.get(name)
        reason = self.denial(op, ctx, enabled_flags=enabled_flags)
        if reason is not None:
            raise OperationDenied(reason)
        try:
            inp = payload if isinstance(payload, op.input) else op.input.model_validate(payload or {})
        except ValidationError as exc:
            raise ValueError(str(exc)) from exc
        result = await op.handler(ctx, inp)
        if not isinstance(result, op.output):
            result = op.output.model_validate(result)
        return result.model_dump(mode="json")


_registry: CapabilityRegistry | None = None


def get_registry() -> CapabilityRegistry:
    """Process-wide registry. Harness-side modules self-register on first use;
    the gateway adds its own (auth sessions, channels, infra) at startup."""
    global _registry
    if _registry is None:
        _registry = CapabilityRegistry()
        from deerflow.capabilities.modules import register_builtin_modules

        register_builtin_modules(_registry)
    return _registry


def reset_registry() -> None:
    global _registry
    _registry = None
