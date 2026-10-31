"""One ACP availability check (``deerflow.runtimes.registry.acp_binary_available``).

The runtimes registry, the ``acp.*`` capability and the integrations
adapter each checked the launcher themselves, and the adapter alone took
``command.split()[0]`` — so the same agent could read as available on the
Integrations page and missing on the Runtimes page.
"""

from __future__ import annotations

import shutil

import pytest

from deerflow.config.acp_config import ACPAgentConfig
from deerflow.runtimes.registry import acp_binary_available

PRESENT = "sh"


@pytest.fixture(autouse=True)
def _need_sh():
    if shutil.which(PRESENT) is None:
        pytest.skip("needs `sh` on PATH")


def test_command_is_the_executable_not_a_command_line():
    assert acp_binary_available(ACPAgentConfig(command=PRESENT, description="d")) is True
    assert acp_binary_available(ACPAgentConfig(command=f"{PRESENT} -c true", description="d")) is False
    assert acp_binary_available(ACPAgentConfig(command="definitely-not-on-path-xyz", description="d")) is False


def test_every_surface_agrees(monkeypatch):
    from deerflow.capabilities.modules import acp as acp_module
    from deerflow.integrations import adapters
    from deerflow.integrations.health import IntegrationStatus
    from deerflow.runtimes.registry import RuntimeRegistry

    agents = {"good": ACPAgentConfig(command=PRESENT, description="d"), "split": ACPAgentConfig(command=f"{PRESENT} -c true", description="d")}
    monkeypatch.setattr(adapters, "_acp_agents", lambda: agents)

    by_adapter = {r.display_name: r.status is IntegrationStatus.HEALTHY for r in adapters.acp_adapter()}
    by_capability = {name: acp_module._describe(name, cfg)["binary_on_path"] for name, cfg in agents.items()}
    reg = RuntimeRegistry(acp_agents=agents)
    by_runtime = {name: reg.info(name).binary_on_path for name in agents}
    assert by_adapter == by_capability == by_runtime == {"good": True, "split": False}
