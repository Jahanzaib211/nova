"""Audit engine for the Nova Execution Kernel.

Phase C7 — tamper-evident execution history.  Every request/result pair
is appended to a bounded ring buffer as an ``ExecutionRecord``.  Records
are hash-chained (sha256 over the previous hash + canonical record JSON)
so any mutation of history is detectable via ``verify_chain``.

Environment variable *values* are never recorded — only key names.
"""

from __future__ import annotations

import hashlib
import json
import logging
import threading
from collections import deque
from dataclasses import replace

from deerflow.execution.models import ExecutionRecord, ExecutionRequest, ExecutionResult

logger = logging.getLogger(__name__)


def _hash_record(prev_hash: str, record_json: str) -> str:
    return hashlib.sha256((prev_hash + record_json).encode("utf-8")).hexdigest()


class AuditEngine:
    """Bounded, hash-chained execution audit trail."""

    def __init__(self, max_records: int = 10_000) -> None:
        self._records: deque[ExecutionRecord] = deque(maxlen=max_records)
        self._lock = threading.Lock()
        self._seq = 0
        self._last_hash = ""

    def record(self, request: ExecutionRequest, result: ExecutionResult) -> ExecutionRecord:
        """Append one execution to the trail and return the sealed record."""
        with self._lock:
            self._seq += 1
            base = ExecutionRecord(
                execution_id=request.execution_id,
                execution_class=request.execution_class.value,
                argv=tuple(request.argv),
                status=result.status.value,
                exit_code=result.exit_code,
                duration_ms=result.duration_ms,
                intent=request.intent,
                cwd=request.cwd,
                env_keys=tuple(sorted(request.env)) if request.env else (),
                correlation_id=request.correlation_id,
                run_id=request.run_id,
                thread_id=request.thread_id,
                started_at=result.started_at,
                finished_at=result.finished_at,
                error=result.error or "",
                seq=self._seq,
                prev_hash=self._last_hash,
            )
            payload = base.to_dict()
            payload.pop("record_hash")
            record_json = json.dumps(payload, sort_keys=True, default=str)
            sealed = replace(base, record_hash=_hash_record(self._last_hash, record_json))
            self._last_hash = sealed.record_hash
            self._records.append(sealed)
            return sealed

    def read_recent(self, limit: int = 100) -> list[ExecutionRecord]:
        with self._lock:
            return list(self._records)[-limit:]

    def find(self, execution_id: str) -> ExecutionRecord | None:
        with self._lock:
            for record in reversed(self._records):
                if record.execution_id == execution_id:
                    return record
        return None

    def verify_chain(self) -> bool:
        """Recompute the hash chain; True iff history is untampered."""
        with self._lock:
            records = list(self._records)
        prev = records[0].prev_hash if records else ""
        for record in records:
            if record.prev_hash != prev:
                return False
            payload = record.to_dict()
            payload.pop("record_hash")
            record_json = json.dumps(payload, sort_keys=True, default=str)
            if _hash_record(prev, record_json) != record.record_hash:
                return False
            prev = record.record_hash
        return True

    @property
    def size(self) -> int:
        with self._lock:
            return len(self._records)
