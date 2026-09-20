"""Declarative capability types.

A feature declares its operations once as a :class:`CapabilityModule`. The
registry derives every surface from that declaration — harness tools for the
lead agent, tools on Nova's own MCP server for external harnesses (Claude
Code, OpenClaw), the generic ``/api/capabilities/ops`` endpoint for the UI,
feature flags and the pinned contract — so a capability cannot exist on one
surface and be missing on another.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any, Literal

from pydantic import BaseModel

OpKind = Literal["read", "write", "execute", "secret", "admin"]

# Kinds a harness tool may carry. ``secret`` (reveals or writes credentials)
# and ``admin`` (operator-only) are never handed to a model as a tool; they
# stay behind the session-authenticated API and the ops token.
HARNESS_TOOL_KINDS: frozenset[str] = frozenset({"read", "write", "execute"})


@dataclass(frozen=True)
class OpContext:
    """Who is invoking, from where. Built by each surface from its own auth."""

    user_id: str
    is_admin: bool = False
    thread_id: str | None = None
    #: "api" | "harness" | "mcp" — for audit lines and per-surface policy.
    surface: str = "api"
    extras: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class ModuleStatus:
    configured: bool
    healthy: bool
    detail: str | None = None


OpHandler = Callable[[OpContext, Any], Awaitable[BaseModel]]
StatusProbe = Callable[[], Awaitable[ModuleStatus]]


@dataclass(frozen=True)
class Operation:
    name: str
    kind: OpKind
    input: type[BaseModel]
    output: type[BaseModel]
    handler: OpHandler
    description: str
    flag: str | None = None
    harness: bool = True
    mcp: bool = True
    admin_only: bool = False

    @property
    def module_id(self) -> str:
        return self.name.split(".", 1)[0]

    @property
    def tool_name(self) -> str:
        """Tool-safe name: ``jobs.list`` → ``jobs__list`` (dots are not allowed by every provider)."""
        return self.name.replace(".", "__")


@dataclass(frozen=True)
class CapabilityModule:
    id: str
    title: str
    operations: list[Operation]
    status: StatusProbe
    flag: str | None = None
    config_key: str | None = None
    description: str = ""


class OperationNotFound(KeyError):
    pass


class OperationDenied(PermissionError):
    pass
