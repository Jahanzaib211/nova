"""Integration health vocabulary and result shape.

Pinned to ``contracts/integrations_health_contract.json`` by
``tests/test_integrations_health_contract.py``. ``scripts/inventory.py``
(host side) and the frontend Settings > Integrations page read the same
file, so the ops console and the in-app page never disagree on what
"degraded" means.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any


class IntegrationStatus(str, Enum):
    HEALTHY = "healthy"
    DEGRADED = "degraded"
    DOWN = "down"
    UNKNOWN = "unknown"
    DISABLED = "disabled"


class IntegrationKind(str, Enum):
    MCP_SERVER = "mcp_server"
    SKILL = "skill"
    ACP_AGENT = "acp_agent"
    LLM_GATEWAY = "llm_gateway"
    MAIL = "mail"
    CRM = "crm"
    HELPDESK = "helpdesk"
    SEARCH = "search"
    CRAWLER = "crawler"
    BROWSER = "browser"
    DATABASE = "database"
    AGENT_GATEWAY = "agent_gateway"


INTEGRATION_STATUSES: tuple[str, ...] = tuple(s.value for s in IntegrationStatus)
INTEGRATION_KINDS: tuple[str, ...] = tuple(k.value for k in IntegrationKind)


@dataclass(slots=True)
class HealthResult:
    """One probed integration, in the contract's ``item_schema`` shape."""

    id: str
    kind: IntegrationKind
    display_name: str
    status: IntegrationStatus
    endpoint: str | None = None
    latency_ms: float | None = None
    checked_at: str | None = None
    detail: str | None = None
    capabilities: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "kind": self.kind.value,
            "display_name": self.display_name,
            "endpoint": self.endpoint,
            "status": self.status.value,
            "latency_ms": self.latency_ms,
            "checked_at": self.checked_at,
            "detail": self.detail,
            "capabilities": list(self.capabilities),
        }
