"""Runtime registry types.

A *runtime* is what runs a chat turn. ``native`` is the LangGraph lead
agent; every ``acp_agents`` entry (``claude_code``, ``openclaw``) is a
runtime too, reached over the Agent Client Protocol. Mirrors OpenClaw's
``agentRuntime.id``: selected per model, overridable per chat.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any, Literal

from deerflow.config.acp_config import ACPPermissionPolicy

NATIVE = "native"

PermissionMode = Literal["full", "standard", "plan"]
PERMISSION_MODES: tuple[PermissionMode, ...] = ("full", "standard", "plan")
DEFAULT_MODE: PermissionMode = "standard"


@dataclass(frozen=True)
class PermissionPreset:
    mode: PermissionMode
    auto_approve: bool
    policy: ACPPermissionPolicy
    label: str
    description: str


def policy_for_mode(mode: str) -> PermissionPreset:
    """The ACP permission policy a chat-level mode expands to.

    ``full``     — "Default (Full Access)": everything auto-approved except
                   deletes.
    ``standard`` — reads, searches, fetches and thinking auto-approved;
                   edits and commands are denied (and reported in the
                   transcript) until the user picks ``full``.
    ``plan``     — read-only: nothing that changes the workspace.
    """
    if mode == "full":
        return PermissionPreset(
            "full",
            True,
            ACPPermissionPolicy(allow_kinds=["read", "search", "fetch", "think", "edit", "move", "execute", "switch_mode", "other"], deny_kinds=["delete"]),
            "Default (Full Access)",
            "Everything except deletes is approved automatically.",
        )
    if mode == "standard":
        return PermissionPreset("standard", False, ACPPermissionPolicy(allow_kinds=["read", "search", "fetch", "think"], deny_kinds=[]), "Standard", "Reads and searches run; edits and commands are denied and shown in the transcript.")
    if mode == "plan":
        return PermissionPreset(
            "plan", False, ACPPermissionPolicy(allow_kinds=["read", "search", "fetch", "think"], deny_kinds=["edit", "delete", "move", "execute"]), "Plan (read-only)", "The agent may only look; nothing in the workspace changes."
        )
    raise ValueError(f"unknown permission mode {mode!r}; one of {', '.join(PERMISSION_MODES)}")


@dataclass(frozen=True)
class Account:
    """An auth profile a runtime can run under. Presence only — never a value."""

    id: str
    label: str
    kind: Literal["claude-login", "api-key", "gateway-token", "configured-models"]
    available: bool
    detail: str = ""

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class RuntimeSelection:
    runtime: str
    account: str
    permission_mode: str
    source: Literal["thread", "model", "default"]


@dataclass(frozen=True)
class RuntimeHealth:
    runtime: str
    account: str
    ok: bool
    latency_ms: int | None = None
    detail: str = ""
    checked_at: str = ""


@dataclass(frozen=True)
class RuntimeInfo:
    id: str
    kind: Literal["native", "acp"]
    label: str
    description: str
    command: list[str] = field(default_factory=list)
    binary_on_path: bool = True
    accounts: list[Account] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["accounts"] = [a.as_dict() for a in self.accounts]
        return d
