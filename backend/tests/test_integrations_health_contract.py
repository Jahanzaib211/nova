"""Contract tests for ``deerflow.integrations.health``.

The integrations status/kind vocabulary and the health item shape are shared
with the frontend (Settings > Integrations) and with ``scripts/inventory.py``
through ``contracts/integrations_health_contract.json``.
"""

from __future__ import annotations

import json
from pathlib import Path

from deerflow.integrations.health import (
    INTEGRATION_KINDS,
    INTEGRATION_STATUSES,
    HealthResult,
    IntegrationKind,
    IntegrationStatus,
)

_REPO_ROOT = Path(__file__).resolve().parents[2]
_CONTRACT_PATH = _REPO_ROOT / "contracts" / "integrations_health_contract.json"


def _load_contract() -> dict:
    return json.loads(_CONTRACT_PATH.read_text(encoding="utf-8"))


def test_contract_file_exists():
    assert _CONTRACT_PATH.is_file(), f"missing shared fixture: {_CONTRACT_PATH}"


def test_statuses_match_contract():
    contract = _load_contract()
    assert list(INTEGRATION_STATUSES) == contract["statuses"]
    assert {s.value for s in IntegrationStatus} == set(contract["statuses"])


def test_kinds_match_contract():
    contract = _load_contract()
    assert list(INTEGRATION_KINDS) == contract["kinds"]
    assert {k.value for k in IntegrationKind} == set(contract["kinds"])


def test_health_result_serialises_to_item_schema():
    """``HealthResult.to_dict()`` must emit exactly the contract's item keys."""
    schema = _load_contract()["item_schema"]
    result = HealthResult(
        id="ollama",
        kind=IntegrationKind.LLM_GATEWAY,
        display_name="Ollama",
        endpoint="http://host.docker.internal:11434",
        status=IntegrationStatus.HEALTHY,
        latency_ms=12.5,
        checked_at="2026-09-18T00:00:00+00:00",
        detail=None,
        capabilities=["chat", "embeddings"],
    )
    item = result.to_dict()
    assert set(item) == set(schema["properties"])
    for key in schema["required"]:
        assert item[key] is not None, key
    assert item["kind"] == "llm_gateway"
    assert item["status"] == "healthy"
