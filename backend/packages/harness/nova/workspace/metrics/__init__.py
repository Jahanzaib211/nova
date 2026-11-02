"""Metrics — in-memory metrics collector for the Workspace Intelligence Kernel.

Phase C9 — collects counters, histograms, and gauges for WIK operations.
Thread-safe.  Metrics reset on process restart.

Usage::

    from deerflow.workspace.metrics import WIKMetrics

    metrics = WIKMetrics()
    metrics.record_scan(file_count=761, project_count=3, symbol_count=17394, duration_ms=850.0)
    metrics.record_cache_hit()
    print(metrics.snapshot())
"""

from __future__ import annotations

import threading
import time
from collections import defaultdict
from dataclasses import dataclass, field


@dataclass
class WIKMetrics:
    """Thread-safe metrics collector for WIK operations.

    Tracks:
    - Scan counts, durations, file/project/symbol counts
    - Cache hits/misses
    - Plan counts, step counts, validation failures
    - Symbol search counts and result sizes
    """

    _lock: threading.Lock = field(default_factory=threading.Lock)

    _scan_count: int = 0
    _scan_duration_ms: list[float] = field(default_factory=list)
    _file_counts: list[int] = field(default_factory=list)
    _project_counts: list[int] = field(default_factory=list)
    _symbol_counts: list[int] = field(default_factory=list)
    _language_distribution: dict[str, int] = field(default_factory=lambda: defaultdict(int))

    _cache_hits: int = 0
    _cache_misses: int = 0

    _plan_count: int = 0
    _plan_valid_count: int = 0
    _plan_invalid_count: int = 0
    _plan_step_counts: list[int] = field(default_factory=list)

    _symbol_search_count: int = 0
    _symbol_search_results: list[int] = field(default_factory=list)
    _symbol_search_duration_ms: list[float] = field(default_factory=list)

    def record_scan(
        self,
        file_count: int,
        project_count: int,
        symbol_count: int,
        duration_ms: float,
        language_distribution: dict[str, float] | None = None,
    ) -> None:
        with self._lock:
            self._scan_count += 1
            self._scan_duration_ms.append(duration_ms)
            self._file_counts.append(file_count)
            self._project_counts.append(project_count)
            self._symbol_counts.append(symbol_count)
            if language_distribution:
                for lang, fraction in language_distribution.items():
                    self._language_distribution[lang] = int(fraction * symbol_count)

    def record_scan_failed(self) -> None:
        with self._lock:
            self._scan_count += 1

    def record_cache_hit(self) -> None:
        with self._lock:
            self._cache_hits += 1

    def record_cache_miss(self) -> None:
        with self._lock:
            self._cache_misses += 1

    def record_plan_built(self, step_count: int, is_valid: bool) -> None:
        with self._lock:
            self._plan_count += 1
            self._plan_step_counts.append(step_count)
            if is_valid:
                self._plan_valid_count += 1
            else:
                self._plan_invalid_count += 1

    def record_symbol_search(self, results_count: int, duration_ms: float) -> None:
        with self._lock:
            self._symbol_search_count += 1
            self._symbol_search_results.append(results_count)
            self._symbol_search_duration_ms.append(duration_ms)

    def snapshot(self) -> dict:
        """Return a snapshot of all metrics as a plain dict."""
        with self._lock:
            scan_dur = self._scan_duration_ms
            symbol_search_dur = self._symbol_search_duration_ms
            total_cache = self._cache_hits + self._cache_misses
            return {
                "scan": {
                    "count": self._scan_count,
                    "avg_duration_ms": (sum(scan_dur) / len(scan_dur)) if scan_dur else 0.0,
                    "max_duration_ms": max(scan_dur) if scan_dur else 0.0,
                    "min_duration_ms": min(scan_dur) if scan_dur else 0.0,
                    "avg_files": (sum(self._file_counts) / len(self._file_counts)) if self._file_counts else 0.0,
                    "avg_projects": (sum(self._project_counts) / len(self._project_counts)) if self._project_counts else 0.0,
                    "avg_symbols": (sum(self._symbol_counts) / len(self._symbol_counts)) if self._symbol_counts else 0.0,
                    "max_symbols": max(self._symbol_counts) if self._symbol_counts else 0,
                },
                "cache": {
                    "hits": self._cache_hits,
                    "misses": self._cache_misses,
                    "hit_rate": (self._cache_hits / total_cache) if total_cache > 0 else 0.0,
                },
                "plan": {
                    "count": self._plan_count,
                    "valid": self._plan_valid_count,
                    "invalid": self._plan_invalid_count,
                    "avg_steps": (sum(self._plan_step_counts) / len(self._plan_step_counts)) if self._plan_step_counts else 0.0,
                },
                "symbol_search": {
                    "count": self._symbol_search_count,
                    "avg_results": (sum(self._symbol_search_results) / len(self._symbol_search_results)) if self._symbol_search_results else 0.0,
                    "avg_duration_ms": (sum(symbol_search_dur) / len(symbol_search_dur)) if symbol_search_dur else 0.0,
                },
                "language_distribution": dict(self._language_distribution),
            }
