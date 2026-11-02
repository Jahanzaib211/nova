"""The retention job's Postgres SQL must only name columns LangGraph's schema has.

Same guard as ``test_prune_checkpoints_schema.py`` (the host script once joined
``checkpoint_blobs`` on a ``checkpoint_id`` column that does not exist, which
aborted every run), applied to :mod:`nova.persistence.checkpoint_retention`.
Read from LangGraph's own migrations, so a schema change upstream fails here.
"""

from __future__ import annotations

import re

import pytest

from nova.persistence import checkpoint_retention as cr


@pytest.fixture(scope="module")
def schema() -> dict[str, set[str]]:
    base = pytest.importorskip("langgraph.checkpoint.postgres.base", reason="langgraph-checkpoint-postgres extra not installed")
    tables: dict[str, set[str]] = {}
    for statement in base.MIGRATIONS:
        match = re.search(r"CREATE TABLE IF NOT EXISTS (\w+)\s*\((.*?)\n\);", statement, re.DOTALL)
        if not match:
            continue
        columns = set()
        for line in match.group(2).splitlines():
            line = line.strip().rstrip(",")
            if line and not line.upper().startswith(("PRIMARY KEY", "FOREIGN KEY", "CONSTRAINT")):
                columns.add(line.split()[0])
        tables[match.group(1)] = columns
    for statement in base.MIGRATIONS:
        # ALTER TABLE ... ADD COLUMN migrations extend the tables above.
        for table, column in re.findall(r"ALTER TABLE (\w+) ADD COLUMN (?:IF NOT EXISTS )?(\w+)", statement):
            tables.setdefault(table, set()).add(column)
    return tables


CTE_COLUMNS = {
    "thread_newest": {"thread_id", "newest"},
    "protected": {"thread_id"},
    "ranked": {"thread_id", "checkpoint_ns", "checkpoint_id", "recency_rank"},
    "doomed": {"thread_id", "checkpoint_ns", "checkpoint_id"},
    "survivor_versions": {"thread_id", "checkpoint_ns", "channel", "version"},
    "channel_max": {"thread_id", "checkpoint_ns", "channel", "max_version"},
    "runs": {"thread_id", "status"},
}


def _unknown_refs(sql: str, schema: dict[str, set[str]]) -> list[str]:
    known = {**schema, **CTE_COLUMNS}
    aliases: dict[str, set[str]] = {}
    for table, alias in re.findall(r"(?:FROM|JOIN|DELETE FROM)\s+(\w+)\s+(?:AS\s+)?(\w+)", sql, re.IGNORECASE):
        if table in known and alias.upper() not in {"AS", "ON", "WHERE", "GROUP", "ORDER", "CROSS", "JOIN"}:
            aliases[alias] = known[table]
    assert aliases, "no aliases resolved; the check would be vacuous"
    return [f"{a}.{c}" for a, cols in aliases.items() for c in re.findall(rf"\b{a}\.(\w+)", sql) if c not in cols]


@pytest.mark.parametrize(
    "sql",
    [
        cr._doomed_cte(cr._POSTGRES, with_runs=True) + "SELECT COUNT(*) FROM doomed",
        cr._PG_BLOB_GC_ONE,
        cr._doomed_cte(cr._POSTGRES, with_runs=True) + cr._PG_BLOB_ESTIMATE_TAIL,
    ],
    ids=["doomed", "blob_gc", "blob_estimate"],
)
def test_every_column_reference_resolves(schema, sql):
    assert not _unknown_refs(sql, schema)


def test_tables_match_langgraph(schema):
    assert {"checkpoints", "checkpoint_blobs", "checkpoint_writes"} <= set(schema)
    assert "checkpoint_id" not in schema["checkpoint_blobs"]
    assert "checkpoint_id" in schema["checkpoint_writes"]


def test_blob_gc_is_scoped_and_guarded():
    gc = cr._PG_BLOB_GC_ONE
    assert "b.thread_id = :thread_id" in gc and "b.checkpoint_ns = :checkpoint_ns"
    assert "NOT EXISTS" in gc, "only blobs no survivor references"
    assert "max_version" in gc, "never a blob newer than every survivor (an in-flight step)"
    assert "'^[0-9]{32}'" in gc, "only zero-padded versions are compared lexically"
