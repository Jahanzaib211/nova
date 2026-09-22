"""Configuration for the runtime registry (Claude Code / OpenClaw as chat runtimes)."""

from __future__ import annotations

from pydantic import BaseModel, Field


class RuntimesConfig(BaseModel):
    enabled: bool = Field(default=False, description="Let a chat run on an ACP runtime (claude_code, openclaw) instead of the native lead agent. Requires the acp_agents overlays (NOVA_ACP_AGENTS=1).")
    default: str = Field(default="native", description="Runtime used when neither the chat nor the model chooses one: `native` or an acp_agents name.")
    nova_mcp_url: str = Field(default="http://127.0.0.1:8001/api/mcp/nova", description="Where the ACP runtime reaches Nova's own MCP server from inside the gateway container (uvicorn listens on 8001 there; 2026 is the host port).")
    claude_login_dir: str | None = Field(default=None, description="Directory holding the mounted Claude login (`.credentials.json`); default ~/.claude in the gateway (/root/.claude with the cli-auth overlay). Presence only — never read.")
    openclaw_token_file: str | None = Field(default=None, description="Path of the OpenClaw gateway token file mounted by docker-compose.acp.yaml; default /run/nova/openclaw_token.")
    turn_timeout_seconds: float = Field(default=1800.0, ge=30.0, description="Wall-clock budget for one chat turn on an ACP runtime.")
    delegate_model: str | None = Field(
        default=None,
        description="Model Nova's subagents run on when an ACP runtime delegates work through agents.delegate without naming one (a job has no parent model to inherit). Default: the deployment's default model — which may be a different provider from the runtime's.",
    )
    fallback_to_native: bool = Field(
        default=True,
        description="When the selected ACP runtime cannot complete a turn (usage cap hit, adapter crashed, timed out), hand the same turn to the native Nova agent — full tool suite (sandbox, subagents, skills) — instead of returning the failure as the reply.",
    )
