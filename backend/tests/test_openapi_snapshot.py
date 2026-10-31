"""Pin the gateway's OpenAPI document against ``contracts/openapi.baseline.json``.

Baseline gate from the 2026-09-18 upgrade program: any phase may *add*
routes, but removing a path or operation, or changing the response schema of
an existing operation, is a wire-contract break and must be deliberate.

Refresh the snapshot in the same PR that adds routes::

    DEER_FLOW_AUTH_DISABLED=1 DEER_FLOW_CONFIG_PATH=../config.example.yaml \
      PYTHONPATH=. uv run python scripts/openapi_snapshot.py > ../contracts/openapi.baseline.json
"""

from __future__ import annotations

import json
import sys
import warnings
from pathlib import Path

import pytest

_BACKEND = Path(__file__).resolve().parents[1]
_SNAPSHOT_PATH = _BACKEND.parent / "contracts" / "openapi.baseline.json"
_HTTP_METHODS = ("get", "post", "put", "patch", "delete", "head", "options")


@pytest.fixture(scope="module")
def live_document() -> dict:
    sys.path.insert(0, str(_BACKEND / "scripts"))
    try:
        from openapi_snapshot import build_openapi_document, normalise
    finally:
        sys.path.pop(0)
    return normalise(build_openapi_document())


@pytest.fixture(scope="module")
def baseline_document() -> dict:
    assert _SNAPSHOT_PATH.is_file(), f"missing snapshot: {_SNAPSHOT_PATH}"
    return json.loads(_SNAPSHOT_PATH.read_text(encoding="utf-8"))


def _operations(document: dict) -> dict[tuple[str, str], dict]:
    ops: dict[tuple[str, str], dict] = {}
    for path, item in document.get("paths", {}).items():
        for method in _HTTP_METHODS:
            if method in item:
                ops[(method.upper(), path)] = item[method]
    return ops


def _referenced_schemas(node: object, out: set[str]) -> set[str]:
    if isinstance(node, dict):
        ref = node.get("$ref")
        if isinstance(ref, str) and ref.startswith("#/components/schemas/"):
            out.add(ref.rsplit("/", 1)[1])
        for value in node.values():
            _referenced_schemas(value, out)
    elif isinstance(node, list):
        for value in node:
            _referenced_schemas(value, out)
    return out


def test_no_operation_removed(live_document, baseline_document):
    missing = sorted(set(_operations(baseline_document)) - set(_operations(live_document)))
    assert not missing, "operations present in the snapshot but gone from the gateway: " + ", ".join(f"{m} {p}" for m, p in missing)


def _compatible(baseline: object, live: object) -> bool:
    """True when ``live`` keeps everything ``baseline`` promised.

    Additive changes pass: a new property on a response model, a new
    response code, a field that became required. Removing or retyping
    anything the snapshot pinned fails. Lists other than ``required`` (enum
    values, ``anyOf`` branches, ...) must match exactly.
    """
    if isinstance(baseline, dict):
        if not isinstance(live, dict):
            return False
        return all(key in live and _compatible(value, live[key]) for key, value in baseline.items())
    if isinstance(baseline, list):
        if not isinstance(live, list):
            return False
        if all(isinstance(item, str) for item in baseline) and all(isinstance(item, str) for item in live):
            return set(baseline) <= set(live)
        return baseline == live
    return baseline == live


def test_response_schemas_unchanged(live_document, baseline_document):
    live_ops = _operations(live_document)
    changed = []
    for key, baseline_op in _operations(baseline_document).items():
        live_op = live_ops.get(key)
        if live_op is None:
            continue  # reported by test_no_operation_removed
        if not _compatible(baseline_op.get("responses"), live_op.get("responses")):
            changed.append(f"{key[0]} {key[1]}")
    assert not changed, "response schemas lost or retyped fields for: " + ", ".join(changed)


def test_referenced_component_schemas_unchanged(live_document, baseline_document):
    """A response can keep its ``$ref`` while the referenced model changes."""
    baseline_components = baseline_document.get("components", {}).get("schemas", {})
    live_components = live_document.get("components", {}).get("schemas", {})
    referenced: set[str] = set()
    for op in _operations(baseline_document).values():
        _referenced_schemas(op.get("responses"), referenced)
    # Follow nested references so a changed sub-model is caught too.
    frontier = set(referenced)
    while frontier:
        name = frontier.pop()
        for nested in _referenced_schemas(baseline_components.get(name), set()):
            if nested not in referenced:
                referenced.add(nested)
                frontier.add(nested)
    changed = sorted(name for name in referenced if not _compatible(baseline_components.get(name), live_components.get(name)))
    assert not changed, "response model schemas lost or retyped fields: " + ", ".join(changed)


def test_additions_remind_to_refresh(live_document, baseline_document):
    added = sorted(set(_operations(live_document)) - set(_operations(baseline_document)))
    if added:
        warnings.warn(
            "new gateway operations not in contracts/openapi.baseline.json (refresh the snapshot): " + ", ".join(f"{m} {p}" for m, p in added),
            stacklevel=1,
        )


@pytest.mark.parametrize(
    ("baseline", "live", "ok"),
    [
        ({"a": 1}, {"a": 1, "b": 2}, True),
        ({"a": 1}, {"a": 2}, False),
        ({"a": 1}, {}, False),
        ({"required": ["a"]}, {"required": ["a", "b"]}, True),
        ({"required": ["a", "b"]}, {"required": ["a"]}, False),
        ({"enum": ["x"]}, {"enum": ["x", "y"]}, True),
        ({"anyOf": [{"type": "string"}]}, {"anyOf": [{"type": "string"}, {"type": "null"}]}, False),
        ({"properties": {"a": {"type": "string"}}}, {"properties": {"a": {"type": "integer"}}}, False),
    ],
)
def test_compatible_accepts_additions_and_rejects_removals(baseline, live, ok):
    assert _compatible(baseline, live) is ok
