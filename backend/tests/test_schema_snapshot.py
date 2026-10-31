"""Pin the ORM schema to ``contracts/schema.baseline.json``.

``create_all`` at boot creates *new* tables but never changes an existing
one, and Alembic only applies what a version file says. A column added to a
model without a migration therefore silently never reaches a live database
(PROD-001). This test turns that into a build failure: any model change
must refresh the snapshot, and the refresh is reviewed next to the migration
that ships it.

    cd backend && PYTHONPATH=. uv run python scripts/schema_snapshot.py > ../contracts/schema.baseline.json
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

_BACKEND = Path(__file__).resolve().parents[1]
_SNAPSHOT = _BACKEND.parent / "contracts" / "schema.baseline.json"


def _live() -> dict:
    sys.path.insert(0, str(_BACKEND / "scripts"))
    try:
        from schema_snapshot import build_schema_document
    finally:
        sys.path.pop(0)
    return build_schema_document()


def test_schema_matches_snapshot():
    assert _SNAPSHOT.is_file(), f"missing {_SNAPSHOT}"
    baseline = json.loads(_SNAPSHOT.read_text(encoding="utf-8"))
    live = _live()
    added = sorted(set(live["tables"]) - set(baseline["tables"]))
    removed = sorted(set(baseline["tables"]) - set(live["tables"]))
    changed = sorted(name for name in set(live["tables"]) & set(baseline["tables"]) if live["tables"][name] != baseline["tables"][name])
    assert not (added or removed or changed), (
        f"ORM schema differs from contracts/schema.baseline.json (added={added}, removed={removed}, changed={changed}). Ship an Alembic migration for the change and refresh the snapshot in the same commit."
    )
