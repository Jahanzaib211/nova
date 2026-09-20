"""Configuration for the runtime registry (Claude Code / OpenClaw as chat runtimes)."""

from __future__ import annotations

from pydantic import BaseModel, Field


class RuntimesConfig(BaseModel):
    enabled: bool = Field(default=False, description="Let a chat run on an ACP runtime (claude_code, openclaw) instead of the native lead agent. Requires the acp_agents overlays (NOVA_ACP_AGENTS=1).")
    default: str = Field(default="native", description="Runtime used when neither the chat nor the model chooses one: `native` or an acp_agents name.")
    nova_mcp_url: str = Field(default="http://127.0.0.1:2026/api/mcp/nova", description="Where the ACP runtime reaches Nova's own MCP server (from inside the gateway container).")
    claude_login_dir: str | None = Field(default=None, description="Directory holding the mounted Claude login (`.credentials.json`); default ~/.claude in the gateway (/root/.claude with the cli-auth overlay). Presence only — never read.")
    openclaw_token_file: str | None = Field(default=None, description="Path of the OpenClaw gateway token file mounted by docker-compose.acp.yaml; default /run/nova/openclaw_token.")
    turn_timeout_seconds: float = Field(default=1800.0, ge=30.0, description="Wall-clock budget for one chat turn on an ACP runtime.")
