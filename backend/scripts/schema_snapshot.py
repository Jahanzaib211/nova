#!/usr/bin/env python3
"""Dump the ORM schema (Base.metadata) as JSON for contracts/schema.baseline.json.

    cd backend && PYTHONPATH=. uv run python scripts/schema_snapshot.py > ../contracts/schema.baseline.json

``tests/test_schema_snapshot.py`` fails when the live models differ from the
snapshot: refresh it in the same commit as the Alembic migration that
applies the change to existing databases (see test_migrations_apply_clean.py
for why ``create_all`` alone is not enough).
"""

from __future__ import annotations

import json
import sys


def build_schema_document() -> dict:
    import deerflow.persistence.models  # noqa: F401 - registers every model
    from deerflow.persistence.base import Base

    tables = {}
    for name, table in sorted(Base.metadata.tables.items()):
        # Test suites declare throwaway models on the same Base (they start
        # with an underscore); they are not part of the product schema.
        if name.startswith("_"):
            continue
        tables[name] = {
            "columns": {
                col.name: {
                    "type": str(col.type),
                    "nullable": col.nullable,
                    "primary_key": col.primary_key,
                }
                for col in table.columns
            },
            "indexes": sorted([idx.name or "", sorted(c.name for c in idx.columns)] for idx in table.indexes),
            "unique": sorted(sorted(c.name for c in uc.columns) for uc in table.constraints if uc.__class__.__name__ == "UniqueConstraint"),
        }
    return {"tables": tables}


def main() -> int:
    json.dump(build_schema_document(), sys.stdout, indent=2, sort_keys=True)
    sys.stdout.write("\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
