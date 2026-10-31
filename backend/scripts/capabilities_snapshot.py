#!/usr/bin/env python3
"""Print the capability registry snapshot (the contract) as JSON.

    cd backend && DEER_FLOW_AUTH_DISABLED=1 DEER_FLOW_CONFIG_PATH=../config.example.yaml \
      PYTHONPATH=. uv run python scripts/capabilities_snapshot.py > ../contracts/capabilities.baseline.json

Pinned by tests/test_capability_registry.py; the frontend client is
generated from the same document by scripts/gen_capabilities_client.py.
"""

from __future__ import annotations

import json
import sys


def main() -> int:
    from app.gateway.capabilities_modules import register_gateway_modules
    from deerflow.capabilities import CapabilityRegistry
    from deerflow.capabilities.modules import register_builtin_modules

    reg = CapabilityRegistry()
    register_builtin_modules(reg)
    register_gateway_modules(reg)
    json.dump(reg.snapshot(), sys.stdout, indent=2, sort_keys=True)
    sys.stdout.write("\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
