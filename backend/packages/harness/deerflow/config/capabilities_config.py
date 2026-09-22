"""Configuration for the capability registry surfaces."""

from __future__ import annotations

from pydantic import BaseModel, Field


class McpServerConfig(BaseModel):
    enabled: bool = Field(default=True, description="Serve Nova's capabilities as an MCP server at /api/mcp/nova for external harnesses (Claude Code, OpenClaw). Auth is a harness token (Settings › Devices).")


class CapabilitiesConfig(BaseModel):
    """Settings for how declared capabilities are exposed.

    Which capabilities become *lead-agent tools* is not decided here but by
    ``tool_groups`` (add ``nova:<module>`` entries), so operators gate Nova's
    own capabilities exactly like ``web`` or ``bash``.
    """

    mcp_server: McpServerConfig = Field(default_factory=McpServerConfig)
