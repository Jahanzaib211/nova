"""The pruner's SQL must only name columns LangGraph's schema actually has.

`prune-checkpoints.py` deleted from `checkpoint_writes` and `checkpoint_blobs`
with the same three-column join, on the assumption that both are keyed by
`checkpoint_id`. Only the first is. `checkpoint_blobs` is keyed
`(thread_id, checkpoint_ns, channel, version)` and has no `checkpoint_id`
column at all, so every scheduled run raised

    psycopg.errors.UndefinedColumn: column c.checkpoint_id does not exist

*after* deleting from checkpoint_writes -- which aborted the transaction, so the
commit never ran and nothing was pruned. The job reported one truncated
"Traceback (most recent call last):" line a day while the checkpoint table grew
without bound: the same failure mode as the 59 GB outage the script exists to
prevent.

There were no tests for this file. These read LangGraph's own migration SQL, so
they also fail if a future LangGraph release changes the schema underneath us.
"""

from __future__ import annotations

import importlib.util
import re
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "prune-checkpoints.py"


@pytest.fixture(scope="module")
def pruner():
    spec = importlib.util.spec_from_file_location("prune_checkpoints", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def schema() -> dict[str, set[str]]:
    """Real column sets, parsed from LangGraph's own CREATE TABLE statements."""
    # The Postgres checkpointer is an optional extra (`uv sync --extra
    # postgres`); without it there is no schema to pin against.
    base = pytest.importorskip("langgraph.checkpoint.postgres.base", reason="langgraph-checkpoint-postgres extra not installed")

    tables: dict[str, set[str]] = {}
    for statement in base.MIGRATIONS:
        match = re.search(
            r"CREATE TABLE IF NOT EXISTS (\w+)\s*\((.*?)\n\);",
            statement,
            re.DOTALL,
        )
        if not match:
            continue
        name, body = match.group(1), match.group(2)
        columns = set()
        for line in body.splitlines():
            line = line.strip().rstrip(",")
            if not line or line.upper().startswith(("PRIMARY KEY", "FOREIGN KEY", "CONSTRAINT")):
                continue
            columns.add(line.split()[0])
        tables[name] = columns
    return tables


class TestTheAssumptionThatBrokeIt:
    def test_checkpoint_blobs_has_no_checkpoint_id(self, schema):
        assert "checkpoint_blobs" in schema
        assert "checkpoint_id" not in schema["checkpoint_blobs"], "if LangGraph ever adds this column the blob GC can be simplified, but until then joining on it is the bug this pins"

    def test_checkpoint_writes_does_have_checkpoint_id(self, schema):
        """The other half of the pair, so the fix is not 'stop joining anywhere'."""
        assert "checkpoint_id" in schema["checkpoint_writes"]

    def test_blobs_are_keyed_by_channel_and_version(self, schema):
        assert {"thread_id", "checkpoint_ns", "channel", "version"} <= schema["checkpoint_blobs"]

    def test_the_blob_delete_never_references_checkpoint_id(self, pruner):
        gc = pruner._PG_BLOB_GC
        blob_refs = re.findall(r"\bb\.(\w+)", gc)
        assert blob_refs, "expected the GC to alias checkpoint_blobs as b"
        assert "checkpoint_id" not in blob_refs


class TestEveryColumnReferenceResolves:
    """Walk the pruner's SQL and check each `alias.column` against the schema."""

    # The CTEs the statements define, and the columns each projects.
    CTE_COLUMNS = {
        "doomed": {"thread_id", "checkpoint_ns", "checkpoint_id"},
        "ranked": {"thread_id", "checkpoint_ns", "checkpoint_id", "recency_rank"},
        "survivor_versions": {"thread_id", "checkpoint_ns", "channel", "version"},
    }

    def _alias_map(self, sql: str, schema: dict[str, set[str]]) -> dict[str, set[str]]:
        known = {**schema, **self.CTE_COLUMNS}
        aliases: dict[str, set[str]] = {}
        pattern = r"(?:FROM|JOIN|DELETE FROM|USING)\s+(\w+)\s+(?:AS\s+)?(\w+)"
        for table, alias in re.findall(pattern, sql, re.IGNORECASE):
            if table.lower() in {"lateral"} or alias.upper() in {"AS", "ON", "WHERE", "SET"}:
                continue
            if table in known:
                aliases[alias] = known[table]
        return aliases

    @pytest.mark.parametrize(
        "statement",
        ["_PG_BLOB_GC", "_PG_DELETE_WRITES", "_PG_DELETE_CHECKPOINTS"],
    )
    def test_no_statement_names_a_column_that_does_not_exist(self, pruner, schema, statement):
        sql = getattr(pruner, statement)
        aliases = self._alias_map(sql, schema)
        assert aliases, f"{statement}: no table aliases resolved; the check would be vacuous"

        unknown = [f"{alias}.{column}" for alias, columns in aliases.items() for column in re.findall(rf"\b{alias}\.(\w+)", sql) if column not in columns]
        assert not unknown, f"{statement} references columns that do not exist: {unknown}"


class TestTheGuaranteeTheGcMakes:
    def test_it_only_deletes_blobs_no_survivor_references(self, pruner):
        """The GC must be scoped by a NOT EXISTS over surviving versions --
        deleting by parent checkpoint is exactly what cannot work here."""
        gc = pruner._PG_BLOB_GC
        assert "survivor_versions" in gc
        assert "NOT EXISTS" in gc.upper()
        assert "channel_versions" in gc, "reachability comes from the checkpoint's channel_versions map"

    def test_it_is_scoped_to_threads_that_were_actually_pruned(self, pruner):
        """Without this an in-flight write on an untouched thread is in range."""
        gc = pruner._PG_BLOB_GC
        assert re.search(r"EXISTS\s*\(\s*SELECT 1 FROM doomed", gc), "the delete must be restricted to (thread_id, checkpoint_ns) pairs we pruned"
