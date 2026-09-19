"""ACP (Agent Client Protocol) agent configuration loaded from config.yaml."""

import logging
from collections.abc import Mapping

from pydantic import BaseModel, Field, field_validator

logger = logging.getLogger(__name__)

# ACP ``ToolKind`` vocabulary (acp.schema.ToolKind); the policy is keyed on it.
ACP_TOOL_KINDS: tuple[str, ...] = ("read", "edit", "delete", "move", "search", "execute", "think", "fetch", "switch_mode", "other")


class ACPPermissionPolicy(BaseModel):
    """Which tool kinds an agent may run without a human in the loop.

    ACP agents ask permission per tool call and tell us the call's kind. The
    gateway runs as root with the docker socket, so the default is to deny
    everything; ``allow_kinds`` opens specific kinds (``read``, ``search``,
    ``fetch`` are the usual safe set) and ``deny_kinds`` always wins, even
    over ``auto_approve_permissions``.
    """

    allow_kinds: list[str] = Field(default_factory=list, description="Tool kinds approved automatically (allow_once).")
    deny_kinds: list[str] = Field(default_factory=list, description="Tool kinds always denied, even when auto_approve_permissions is true.")

    @field_validator("allow_kinds", "deny_kinds")
    @classmethod
    def _known_kinds(cls, kinds: list[str]) -> list[str]:
        unknown = [k for k in kinds if k not in ACP_TOOL_KINDS]
        if unknown:
            raise ValueError(f"unknown ACP tool kind(s) {unknown}; expected {', '.join(ACP_TOOL_KINDS)}")
        return kinds

    def decide(self, kind: str | None, *, auto_approve: bool) -> bool:
        """True when a permission request for ``kind`` should be approved."""
        if kind is not None and kind in self.deny_kinds:
            return False
        if auto_approve:
            return True
        return kind is not None and kind in self.allow_kinds


class ACPAgentConfig(BaseModel):
    """Configuration for a single ACP-compatible agent."""

    command: str = Field(description="Command to launch the ACP agent subprocess")
    args: list[str] = Field(default_factory=list, description="Additional command arguments")
    env: dict[str, str] = Field(default_factory=dict, description="Environment variables to inject into the agent subprocess. Values starting with $ are resolved from host environment variables.")
    description: str = Field(description="Description of the agent's capabilities (shown in tool description)")
    model: str | None = Field(default=None, description="Model hint passed to the agent (optional)")
    auto_approve_permissions: bool = Field(
        default=False,
        description=(
            "When True, DeerFlow automatically approves all ACP permission requests from this agent "
            "(allow_once preferred over allow_always). When False (default), all permission requests "
            "are denied — the agent must be configured to operate without requesting permissions."
        ),
    )
    permission_policy: ACPPermissionPolicy = Field(
        default_factory=ACPPermissionPolicy,
        description="Per-tool-kind approvals: allow_kinds are approved automatically, deny_kinds never are (even with auto_approve_permissions).",
    )


_acp_agents: dict[str, ACPAgentConfig] = {}


def get_acp_agents() -> dict[str, ACPAgentConfig]:
    """Get the currently configured ACP agents.

    Returns:
        Mapping of agent name -> ACPAgentConfig.  Empty dict if no ACP agents are configured.
    """
    return _acp_agents


def load_acp_config_from_dict(config_dict: Mapping[str, Mapping[str, object]] | None) -> None:
    """Load ACP agent configuration from a dictionary (typically from config.yaml).

    Args:
        config_dict: Mapping of agent name -> config fields.
    """
    global _acp_agents
    if config_dict is None:
        config_dict = {}
    _acp_agents = {name: ACPAgentConfig(**cfg) for name, cfg in config_dict.items()}
    logger.info("ACP config loaded: %d agent(s): %s", len(_acp_agents), list(_acp_agents.keys()))
