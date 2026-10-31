"""The integrations capability module's health arithmetic.

Pins a fix made on 2026-09-21. The allowlist read
``("healthy", "configured")``: ``configured`` is not a member of
``IntegrationStatus`` at all (dead string, excluded nothing) while ``disabled``
— a deliberate operator choice — was counted as a fault. A proposed widening
would also have admitted ``degraded``, which ``health.py`` defines as
alive-but-broken; that would have reported the module green while a dependency
was down, which is the failure mode gates exist to prevent.
"""

from __future__ import annotations

import pytest

from deerflow.integrations.health import IntegrationStatus


def _status_of(result):
    """Mirror the module's own unwrapping of enum-or-string."""
    return getattr(getattr(result, "status", None), "value", result.status)


class _Result:
    def __init__(self, status):
        self.status = status


def test_integration_status_members_are_what_the_module_assumes():
    """If this vocabulary changes, the allowlist below must be revisited."""
    assert {s.value for s in IntegrationStatus} == {
        "healthy",
        "degraded",
        "down",
        "unknown",
        "disabled",
    }


def test_configured_is_not_a_real_status():
    """The string the old allowlist used. Its absence is the point."""
    assert "configured" not in {s.value for s in IntegrationStatus}


@pytest.mark.parametrize(
    ("status", "counts_as_healthy"),
    [
        (IntegrationStatus.HEALTHY, True),
        (IntegrationStatus.DISABLED, True),  # a choice, not a fault
        (IntegrationStatus.DEGRADED, False),  # alive-but-broken must stay red
        (IntegrationStatus.DOWN, False),
        (IntegrationStatus.UNKNOWN, False),
    ],
)
def test_allowlist_classifies_every_status(status, counts_as_healthy):
    ok = ("healthy", "disabled")
    assert (_status_of(_Result(status)) in ok) is counts_as_healthy


class _Svc:
    def __init__(self, required=True):
        self.required = required


def _registry_with(results, services=None):
    class _Registry:
        config = type("C", (), {"services": services or {}})()

        async def probe_all(self, refresh: bool = False):
            return results

    return _Registry()


@pytest.mark.asyncio
async def test_a_required_degraded_integration_makes_the_module_unhealthy(monkeypatch):
    from deerflow.capabilities.modules import integrations as mod

    results = [
        _Result(IntegrationStatus.HEALTHY),
        _Result(IntegrationStatus.DISABLED),
        _Result(IntegrationStatus.DEGRADED),
    ]
    for r, rid in zip(results, ["ok", "off", "broken"]):
        r.id = rid

    monkeypatch.setattr(mod, "_registry", lambda: _registry_with(results, {"broken": _Svc(required=True)}))
    monkeypatch.setattr(mod, "section_enabled", lambda _name: True)

    status = await mod._status()
    assert status.configured is True
    assert status.healthy is False, "a required degraded integration must not read as healthy"
    assert "broken" in status.detail


@pytest.mark.asyncio
async def test_an_optional_degraded_integration_does_not_pin_the_gate(monkeypatch):
    """Mailcow with no MAILCOW_API_KEY is expected, not an incident.

    It still shows its real status on the card; it just cannot hold the whole
    module yellow forever, because a permanently-yellow gate stops being read.
    """
    from deerflow.capabilities.modules import integrations as mod

    results = [_Result(IntegrationStatus.HEALTHY), _Result(IntegrationStatus.DEGRADED)]
    results[0].id, results[1].id = "ok", "mailcow"

    monkeypatch.setattr(mod, "_registry", lambda: _registry_with(results, {"mailcow": _Svc(required=False)}))
    monkeypatch.setattr(mod, "section_enabled", lambda _name: True)

    status = await mod._status()
    assert status.healthy is True
    assert "optional degraded" in status.detail


@pytest.mark.asyncio
async def test_unknown_ids_default_to_required(monkeypatch):
    """Adapters (MCP servers, skills, ACP agents) are not in config.services."""
    from deerflow.capabilities.modules import integrations as mod

    results = [_Result(IntegrationStatus.DEGRADED)]
    results[0].id = "mcp:something"

    monkeypatch.setattr(mod, "_registry", lambda: _registry_with(results, {}))
    monkeypatch.setattr(mod, "section_enabled", lambda _name: True)

    assert (await mod._status()).healthy is False
