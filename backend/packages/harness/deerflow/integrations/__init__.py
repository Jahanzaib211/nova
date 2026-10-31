"""Integrations registry: what Nova can reach and whether it works.

Health vocabulary is pinned to ``contracts/integrations_health_contract.json``.
"""

from deerflow.integrations.health import HealthResult, IntegrationKind, IntegrationStatus

__all__ = ["HealthResult", "IntegrationKind", "IntegrationStatus", "get_integrations_registry"]


def __getattr__(name: str):
    # Lazy (PEP 562): the registry pulls in httpx and every adapter, which
    # importers of the health vocabulary alone should not pay for.
    if name == "get_integrations_registry":
        from deerflow.integrations.registry import get_integrations_registry

        return get_integrations_registry
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
