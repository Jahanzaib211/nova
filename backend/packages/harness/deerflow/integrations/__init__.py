"""Integrations registry: what Nova can reach and whether it works.

Health vocabulary is pinned to ``contracts/integrations_health_contract.json``.
"""

from deerflow.integrations.health import HealthResult, IntegrationKind, IntegrationStatus

__all__ = ["HealthResult", "IntegrationKind", "IntegrationStatus"]
