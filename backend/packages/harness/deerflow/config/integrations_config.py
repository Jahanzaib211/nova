"""Integrations registry configuration (``integrations:`` in config.yaml).

Hot-reloaded: the registry is rebuilt from the freshest ``AppConfig`` on each
request, so adding a service is a config.yaml edit, not a restart.

Secrets are named, not inlined: ``api_key_env`` is the *name* of an
environment variable, resolved when a probe runs. ``$VAR`` values would be
resolved by ``AppConfig.resolve_env_variables`` at load time and a missing
variable there fails the whole config — for an optional integration that is
the wrong failure mode, so the probe reports "key not set" instead.
"""

from __future__ import annotations

import os

from pydantic import BaseModel, Field, field_validator

from deerflow.integrations.health import INTEGRATION_KINDS


class IntegrationServiceConfig(BaseModel):
    kind: str = Field(description="Contract kind: llm_gateway, mail, crm, helpdesk, search, crawler, browser, database, agent_gateway.")
    url: str = Field(description="Base URL as reachable from the gateway container (host services go through host.docker.internal).")
    display_name: str | None = Field(default=None, description="Card title; defaults to the service id.")
    enabled: bool = Field(default=True, description="False lists the service as disabled without probing it.")
    api_key_env: str | None = Field(default=None, description="Name of the environment variable holding the API key/token (never the value).")
    health_path: str | None = Field(default=None, description="Override the probe path for generic HTTP services.")
    capabilities: list[str] = Field(default_factory=list, description="Static capability chips shown on the card.")

    @field_validator("kind")
    @classmethod
    def _known_kind(cls, value: str) -> str:
        if value not in INTEGRATION_KINDS:
            raise ValueError(f"unknown integration kind {value!r}; expected one of {', '.join(INTEGRATION_KINDS)}")
        return value

    def resolve_api_key(self) -> str | None:
        if not self.api_key_env:
            return None
        value = os.environ.get(self.api_key_env, "").strip()
        return value or None


class IntegrationsConfig(BaseModel):
    enabled: bool = Field(default=False, description="Enable the integrations registry (/api/integrations and Settings › Integrations).")
    probe_cache_seconds: float = Field(default=20.0, ge=0.0, description="Serve cached probe results for this long; ?refresh=1 bypasses it.")
    probe_timeout_seconds: float = Field(default=3.0, gt=0.0, le=30.0, description="Per-service HTTP timeout; probes run concurrently.")
    services: dict[str, IntegrationServiceConfig] = Field(default_factory=dict, description="Services to probe, keyed by id.")
