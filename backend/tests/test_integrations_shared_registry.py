"""One integrations registry per process.

``app.gateway.deps.get_integrations_registry`` and the ``integrations.*``
capability module each built and cached their own registry, so each held a
separate probe cache: a probe on the Settings page did not refresh what the
lead agent's ``integrations__list`` saw, and vice versa.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from deerflow.config.integrations_config import IntegrationsConfig
from deerflow.integrations.registry import get_integrations_registry, reset_integrations_registry


@pytest.fixture(autouse=True)
def _fresh():
    reset_integrations_registry()
    yield
    reset_integrations_registry()


def test_gateway_and_capability_share_one_instance(monkeypatch):
    from app.gateway import deps
    from deerflow.capabilities.modules import integrations as cap

    config = IntegrationsConfig()
    monkeypatch.setattr(deps, "get_config", lambda: SimpleNamespace(integrations=config))
    monkeypatch.setattr("deerflow.config.app_config.get_app_config", lambda: SimpleNamespace(integrations=config))
    request = SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace()))
    assert deps.get_integrations_registry(request) is cap._registry()


def test_rebuilt_only_when_the_section_changes():
    a = IntegrationsConfig()
    first = get_integrations_registry(a)
    assert get_integrations_registry(IntegrationsConfig()) is first
    changed = IntegrationsConfig.model_validate({"enabled": not a.enabled})
    assert get_integrations_registry(changed) is not first


def test_package_exports_the_accessor():
    import deerflow.integrations as pkg

    assert pkg.get_integrations_registry is get_integrations_registry
