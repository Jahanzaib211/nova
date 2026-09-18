#!/usr/bin/env python3
"""Emit the gateway's OpenAPI document without booting the gateway.

``create_app()`` only reads the gateway env config and registers routers; the
lifespan (DB engines, channels, LangGraph runtime, sandbox providers) is never
entered, so nothing here talks to Postgres, Docker or a model provider. That
is what makes this safe to run on the live box.

Usage (from ``backend/``)::

    DEER_FLOW_AUTH_DISABLED=1 DEER_FLOW_CONFIG_PATH=../config.example.yaml \
      PYTHONPATH=. uv run python scripts/openapi_snapshot.py > ../contracts/openapi.baseline.json

``tests/test_openapi_snapshot.py`` compares the live document against that
file: removed paths/operations or changed response schemas fail; additions
pass with a reminder to refresh the snapshot.
"""

from __future__ import annotations

import json
import sys


def build_openapi_document() -> dict:
    from app.gateway.app import create_app

    return create_app().openapi()


def normalise(document: dict) -> dict:
    """Strip fields that vary without an API change (title/description prose)."""
    doc = json.loads(json.dumps(document))
    info = doc.get("info", {})
    info.pop("description", None)
    return doc


def main() -> int:
    doc = normalise(build_openapi_document())
    json.dump(doc, sys.stdout, indent=2, sort_keys=True)
    sys.stdout.write("\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
