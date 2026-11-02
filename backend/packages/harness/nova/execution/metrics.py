"""Metrics for the Nova Execution Kernel.

Phase C7 — per-class counters and latency aggregates.  Pure in-memory,
thread-safe, exported via ``snapshot()`` for the diagnostics/metrics
endpoints.  Mirrors the RecoveryEngine metrics style from Phase C4.
"""

from __future__ import annotations

import threading
from collections import defaultdict
from typing import Any

from deerflow.execution.models import ExecutionClass, ExecutionStatus


class ExecutionMetrics:
    """Counters + latency aggregates keyed by execution class."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._counts: dict[tuple[str, str], int] = defaultdict(int)
        self._latency_sum_ms: dict[str, float] = defaultdict(float)
        self._latency_max_ms: dict[str, float] = defaultdict(float)
        self._completed: dict[str, int] = defaultdict(int)

    def observe(
        self,
        execution_class: ExecutionClass,
        status: ExecutionStatus,
        duration_ms: float,
    ) -> None:
        cls = execution_class.value
        with self._lock:
            self._counts[(cls, status.value)] += 1
            if status.is_terminal:
                self._completed[cls] += 1
                self._latency_sum_ms[cls] += duration_ms
                if duration_ms > self._latency_max_ms[cls]:
                    self._latency_max_ms[cls] = duration_ms

    def snapshot(self) -> dict[str, Any]:
        with self._lock:
            classes: dict[str, Any] = {}
            for (cls, status), count in self._counts.items():
                entry = classes.setdefault(cls, {"by_status": {}})
                entry["by_status"][status] = count
            for cls, entry in classes.items():
                completed = self._completed.get(cls, 0)
                entry["completed"] = completed
                entry["latency_avg_ms"] = self._latency_sum_ms.get(cls, 0.0) / completed if completed else 0.0
                entry["latency_max_ms"] = self._latency_max_ms.get(cls, 0.0)
            total = sum(self._counts.values())
        return {"total": total, "classes": classes}
