"""The runtime registry: which runtimes exist, which accounts each can use,
and which one a given chat turn runs on."""

from __future__ import annotations

import logging
import os
import shutil
import time
from collections.abc import Mapping
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from deerflow.config.acp_config import ACPAgentConfig
from deerflow.runtimes.types import (
    DEFAULT_MODE,
    NATIVE,
    PERMISSION_MODES,
    Account,
    RuntimeHealth,
    RuntimeInfo,
    RuntimeSelection,
    policy_for_mode,
)

logger = logging.getLogger(__name__)

_LABELS = {"claude_code": "Claude Code", "openclaw": "OpenClaw"}


class RuntimeRegistry:
    def __init__(
        self,
        *,
        acp_agents: Mapping[str, ACPAgentConfig],
        default: str = NATIVE,
        claude_login_dir: Path | str | None = None,
        openclaw_token_file: Path | str | None = None,
    ) -> None:
        self._agents = dict(acp_agents)
        self._default = default if default == NATIVE or default in self._agents else NATIVE
        self._claude_login_dir = Path(claude_login_dir) if claude_login_dir else Path(os.environ.get("NOVA_CLAUDE_LOGIN_DIR") or (Path.home() / ".claude"))
        self._openclaw_token_file = Path(openclaw_token_file) if openclaw_token_file else Path(os.environ.get("NOVA_OPENCLAW_TOKEN_FILE") or "/run/nova/openclaw_token")

    # -- inventory --------------------------------------------------------

    def ids(self) -> list[str]:
        return sorted({NATIVE, *self._agents})

    def agent(self, runtime_id: str) -> ACPAgentConfig | None:
        return self._agents.get(runtime_id)

    def accounts_for(self, runtime_id: str) -> list[Account]:
        if runtime_id == NATIVE:
            return [Account(id="configured-models", label="Configured models", kind="configured-models", available=True, detail="config.yaml models")]
        if runtime_id == "claude_code":
            cred = self._claude_login_dir / ".credentials.json"
            return [
                Account(
                    id="claude-login",
                    label="Claude login (your own `claude` session)",
                    kind="claude-login",
                    available=cred.is_file(),
                    detail=f"{cred} mounted read-only" if cred.is_file() else f"{cred} not present — run `claude` on the host and set NOVA_ACP_AGENTS=1",
                ),
                Account(id="anthropic-api-key", label="Anthropic API key", kind="api-key", available=bool(os.environ.get("ANTHROPIC_API_KEY", "").strip()), detail="ANTHROPIC_API_KEY"),
            ]
        if runtime_id == "openclaw":
            tok = self._openclaw_token_file
            return [Account(id="gateway-token", label="OpenClaw gateway token", kind="gateway-token", available=tok.is_file(), detail=str(tok))]
        cfg = self._agents.get(runtime_id)
        return [Account(id="adapter", label=f"{runtime_id} adapter", kind="gateway-token", available=cfg is not None, detail=cfg.description if cfg else "")]

    def resolve_account(self, runtime_id: str, requested: str | None) -> Account:
        accounts = self.accounts_for(runtime_id)
        if requested and requested != "auto":
            for a in accounts:
                if a.id == requested:
                    return a
        return next((a for a in accounts if a.available), accounts[0])

    def info(self, runtime_id: str) -> RuntimeInfo:
        if runtime_id == NATIVE:
            return RuntimeInfo(id=NATIVE, kind="native", label="Nova (native)", description="Nova's own LangGraph lead agent with its tools, skills, memory and sandbox.", accounts=self.accounts_for(NATIVE))
        cfg = self._agents[runtime_id]
        return RuntimeInfo(
            id=runtime_id,
            kind="acp",
            label=_LABELS.get(runtime_id, runtime_id),
            description=cfg.description,
            command=[cfg.command, *(cfg.args or [])],
            binary_on_path=bool(shutil.which(cfg.command)),
            accounts=self.accounts_for(runtime_id),
        )

    def describe(self) -> dict[str, Any]:
        return {
            "default": self._default,
            "runtimes": [self.info(r).as_dict() for r in self.ids()],
            "permission_modes": list(PERMISSION_MODES),
            "modes": [{"id": policy_for_mode(m).mode, "label": policy_for_mode(m).label, "description": policy_for_mode(m).description} for m in PERMISSION_MODES],
        }

    # -- selection --------------------------------------------------------

    def select(self, *, context: Mapping[str, Any] | None, model_runtime: str | None) -> RuntimeSelection:
        """Thread override (run context) > model's ``runtime:`` > default.
        Unknown values degrade to the next level, never raise mid-run."""
        ctx = dict(context or {})
        mode = ctx.get("permission_mode") or DEFAULT_MODE
        if mode not in PERMISSION_MODES:
            mode = DEFAULT_MODE
        account = str(ctx.get("runtime_account") or "auto")
        requested = ctx.get("runtime")
        if requested and (requested == NATIVE or requested in self._agents):
            return RuntimeSelection(runtime=str(requested), account=account, permission_mode=mode, source="thread")
        if model_runtime and (model_runtime == NATIVE or model_runtime in self._agents):
            return RuntimeSelection(runtime=model_runtime, account=account, permission_mode=mode, source="model")
        return RuntimeSelection(runtime=self._default, account=account, permission_mode=mode, source="default")

    # -- probing ----------------------------------------------------------

    async def probe(self, runtime_id: str, account_id: str | None = None, *, timeout: float = 90.0, cwd: str | None = None) -> RuntimeHealth:
        """ "Check model": a one-word round trip through the runtime."""
        started = time.monotonic()
        now = datetime.now(UTC).isoformat()
        account = self.resolve_account(runtime_id, account_id)
        if runtime_id == NATIVE:
            return RuntimeHealth(runtime=NATIVE, account=account.id, ok=True, latency_ms=0, detail="use models.probe for a specific model", checked_at=now)
        cfg = self._agents.get(runtime_id)
        if cfg is None:
            return RuntimeHealth(runtime=runtime_id, account=account.id, ok=False, detail="unknown runtime", checked_at=now)
        if not shutil.which(cfg.command):
            return RuntimeHealth(runtime=runtime_id, account=account.id, ok=False, detail=f"{cfg.command} not on PATH", checked_at=now)
        if not account.available:
            return RuntimeHealth(runtime=runtime_id, account=account.id, ok=False, detail=account.detail, checked_at=now)
        from deerflow.runtimes.acp_transport import run_acp_prompt

        try:
            text = await run_acp_prompt(cfg, "Reply with the single word: ok", cwd=cwd or os.getcwd(), mcp_servers=[], permission=policy_for_mode("plan"), on_text=lambda _sid, _t: None, on_status=lambda _sid, _s: None, timeout=timeout)
            return RuntimeHealth(runtime=runtime_id, account=account.id, ok=True, latency_ms=int((time.monotonic() - started) * 1000), detail=(text or "").strip()[:80], checked_at=now)
        except Exception as exc:
            return RuntimeHealth(runtime=runtime_id, account=account.id, ok=False, latency_ms=int((time.monotonic() - started) * 1000), detail=f"{type(exc).__name__}: {exc}"[:300], checked_at=now)


_registry: RuntimeRegistry | None = None
_registry_source: str | None = None


def get_runtime_registry() -> RuntimeRegistry:
    """Registry for the current config (rebuilt when ``acp_agents``/``runtimes`` change)."""
    global _registry, _registry_source
    from deerflow.config.acp_config import get_acp_agents
    from deerflow.config.app_config import get_app_config

    cfg = get_app_config()
    rt = getattr(cfg, "runtimes", None)
    agents = get_acp_agents() or {}
    source = repr(sorted(agents)) + (rt.model_dump_json() if rt is not None else "")
    if _registry is None or _registry_source != source:
        _registry = RuntimeRegistry(acp_agents=agents, default=getattr(rt, "default", NATIVE), claude_login_dir=getattr(rt, "claude_login_dir", None), openclaw_token_file=getattr(rt, "openclaw_token_file", None))
        _registry_source = source
    return _registry


def reset_runtime_registry() -> None:
    global _registry, _registry_source
    _registry, _registry_source = None, None
