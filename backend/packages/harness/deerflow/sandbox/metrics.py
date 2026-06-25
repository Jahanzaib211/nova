"""In-process metrics for the browser/computer control surface.

Enterprise-grade observability needs:

  - **Counters**: monotonic event totals (browser_check_total, circuit_transitions_total).
  - **Histograms**: latency distributions (browser_check_duration_ms).
  - **Gauges**: point-in-time values (active threads, circuit-open count).
  - **Prometheus text format**: render() for /api/metrics.

Why in-process (no Prometheus client lib)
----------------------------------------
The gateway already has LangSmith integration for trace export. Adding
prometheus_client pulls in protobuf + wsgi + value-tracking. For this
scope (browser subsystem only, ~10 metric series), a hand-rolled
counter/histogram is ~150 LOC, zero new deps, and renders the same
text format Prometheus scrapers expect.

Cardinality is bounded by design: only ``thread_id`` and ``state`` are
used as labels, both with low cardinality (thread_id = sandbox count,
state = 3 values).

Backwards compatibility
-----------------------
New module, no existing import paths change. metrics.py is a stable
name at the harness root — verified via existing test_runtime_paths_env
that no clash.
"""

from __future__ import annotations

import math
import threading
from collections import defaultdict
from dataclasses import dataclass, field
from typing import Iterable


# ---------- counter ----------

@dataclass
class _CounterSeries:
    """One labelled series of a counter.

    The counter value is stored as a float so we can use it for both
    monotonic counts and "elapsed since reset" gauges if needed later.
    """
    labels: tuple[tuple[str, str], ...]
    value: float = 0.0
    # Last touch timestamp (time.time()) — useful for /api/health freshness.
    last_touched: float = 0.0


class Counter:
    """Monotonic counter with optional labels.

    Usage::

        browser_check_total = Counter(
            "browser_check_total",
            "Total browser_check calls, partitioned by outcome.",
            labelnames=("outcome",),
        )
        browser_check_total.inc("ok")
        browser_check_total.inc("error", n=3)
    """

    def __init__(self, name: str, help: str, labelnames: tuple[str, ...] = ()) -> None:
        self.name = name
        self.help = help
        self.labelnames = labelnames
        self._series: dict[tuple[str, ...], _CounterSeries] = {}
        self._lock = threading.Lock()

    def _key(self, labelvalues: tuple[str, ...]) -> tuple[str, ...]:
        if len(labelvalues) != len(self.labelnames):
            raise ValueError(
                f"counter {self.name!r} expects {len(self.labelnames)} label values, "
                f"got {len(labelvalues)}: {labelvalues!r}"
            )
        return labelvalues

    def inc(self, *labelvalues: str, n: float = 1.0) -> None:
        """Increment by ``n`` (default 1)."""
        import time as _time
        key = self._key(labelvalues)
        with self._lock:
            s = self._series.get(key)
            if s is None:
                s = _CounterSeries(labels=tuple(zip(self.labelnames, labelvalues)))
                self._series[key] = s
            s.value += n
            s.last_touched = _time.time()

    def get(self, *labelvalues: str) -> float:
        """Return the current value (0 if no series)."""
        key = self._key(labelvalues)
        with self._lock:
            s = self._series.get(key)
            return s.value if s is not None else 0.0

    def snapshot(self) -> list[_CounterSeries]:
        with self._lock:
            return list(self._series.values())


# ---------- histogram ----------

# Fixed bucket layout in MILLISECONDS. Matches typical browser-check latencies:
#   - p50: 200-500ms (cached page)
#   - p95: 5-10s (cold chromium)
#   - p99: 15-30s (timeout path)
_HISTOGRAM_DEFAULT_BUCKETS_MS: tuple[float, ...] = (
    10.0, 25.0, 50.0, 100.0, 250.0, 500.0,
    1000.0, 2500.0, 5000.0, 10000.0, 30000.0,
    float("+inf"),  # prometheus convention: +Inf bucket catches everything
)


@dataclass
class _HistogramSeries:
    labels: tuple[tuple[str, str], ...]
    count: int = 0
    sum_ms: float = 0.0
    # bucket counts: bucket[i] = number of observations <= buckets[i]
    bucket_counts: list[int] = field(default_factory=list)


class Histogram:
    """Histogram with fixed bucket boundaries.

    Usage::

        browser_check_duration_ms = Histogram(
            "browser_check_duration_ms",
            "Browser check wall-clock duration in milliseconds.",
            labelnames=("engine",),
            buckets_ms=(10, 100, 500, 5000, 30000, float("inf")),
        )
        browser_check_duration_ms.observe(425.0, "cdp")
    """

    def __init__(
        self,
        name: str,
        help: str,
        labelnames: tuple[str, ...] = (),
        buckets_ms: tuple[float, ...] | None = None,
    ) -> None:
        self.name = name
        self.help = help
        self.labelnames = labelnames
        self.buckets = tuple(sorted(buckets_ms)) if buckets_ms else _HISTOGRAM_DEFAULT_BUCKETS_MS
        self._series: dict[tuple[str, ...], _HistogramSeries] = {}
        self._lock = threading.Lock()

    def _key(self, labelvalues: tuple[str, ...]) -> tuple[str, ...]:
        if len(labelvalues) != len(self.labelnames):
            raise ValueError(
                f"histogram {self.name!r} expects {len(self.labelnames)} label values, "
                f"got {len(labelvalues)}: {labelvalues!r}"
            )
        return labelvalues

    def observe(self, value_ms: float, *labelvalues: str) -> None:
        if value_ms < 0 or math.isnan(value_ms):
            return  # ignore invalid observations
        key = self._key(labelvalues)
        with self._lock:
            s = self._series.get(key)
            if s is None:
                s = _HistogramSeries(
                    labels=tuple(zip(self.labelnames, labelvalues)),
                    bucket_counts=[0] * len(self.buckets),
                )
                self._series[key] = s
            s.count += 1
            s.sum_ms += value_ms
            for i, bound in enumerate(self.buckets):
                if value_ms <= bound:
                    s.bucket_counts[i] += 1

    def snapshot(self) -> list[_HistogramSeries]:
        with self._lock:
            return list(self._series.values())


# ---------- gauge ----------

class Gauge:
    """Point-in-time gauge (can go up or down).

    Usage::

        active_browser_checks = Gauge(
            "active_browser_checks",
            "Number of browser_check calls currently in-flight.",
        )
        active_browser_checks.inc(); ... ; active_browser_checks.dec()
    """

    def __init__(self, name: str, help: str, labelnames: tuple[str, ...] = ()) -> None:
        self.name = name
        self.help = help
        self.labelnames = labelnames
        self._series: dict[tuple[str, ...], float] = defaultdict(float)
        self._lock = threading.Lock()

    def set(self, value: float, *labelvalues: str) -> None:
        if len(labelvalues) != len(self.labelnames):
            raise ValueError(
                f"gauge {self.name!r} expects {len(self.labelnames)} label values, "
                f"got {len(labelvalues)}: {labelvalues!r}"
            )
        with self._lock:
            self._series[labelvalues] = float(value)

    def inc(self, *labelvalues: str, n: float = 1.0) -> None:
        with self._lock:
            self._series[labelvalues] = self._series.get(labelvalues, 0.0) + n

    def dec(self, *labelvalues: str, n: float = 1.0) -> None:
        with self._lock:
            self._series[labelvalues] = self._series.get(labelvalues, 0.0) - n

    def snapshot(self) -> dict[tuple[str, ...], float]:
        with self._lock:
            return dict(self._series)


# ---------- registry + renderer ----------

class _Registry:
    """Process-wide metrics registry.

    Lazy-instantiated via ``get_registry()`` so importing this module
    is side-effect-free.
    """

    def __init__(self) -> None:
        self._counters: list[Counter] = []
        self._histograms: list[Histogram] = []
        self._gauges: list[Gauge] = []
        self._lock = threading.Lock()

    def register_counter(self, c: Counter) -> Counter:
        with self._lock:
            self._counters.append(c)
            return c

    def register_histogram(self, h: Histogram) -> Histogram:
        with self._lock:
            self._histograms.append(h)
            return h

    def register_gauge(self, g: Gauge) -> Gauge:
        with self._lock:
            self._gauges.append(g)
            return g

    def render_prometheus(self) -> str:
        """Render all metrics in Prometheus text exposition format.

        See https://prometheus.io/docs/instrumenting/exposition_formats/
        """
        lines: list[str] = []
        with self._lock:
            counters = list(self._counters)
            histograms = list(self._histograms)
            gauges = list(self._gauges)

        for c in counters:
            lines.append(f"# HELP {c.name} {c.help}")
            lines.append(f"# TYPE {c.name} counter")
            for s in c.snapshot():
                labels_str = _format_labels(s.labels)
                lines.append(f"{c.name}{labels_str} {s.value}")

        for g in gauges:
            lines.append(f"# HELP {g.name} {g.help}")
            lines.append(f"# TYPE {g.name} gauge")
            for labelvalues, value in g.snapshot().items():
                labels = tuple(zip(g.labelnames, labelvalues))
                labels_str = _format_labels(labels)
                lines.append(f"{g.name}{labels_str} {value}")

        for h in histograms:
            lines.append(f"# HELP {h.name} {h.help}")
            lines.append(f"# TYPE {h.name} histogram")
            for s in h.snapshot():
                # bucket lines
                cumulative = 0
                for i, bound in enumerate(h.buckets):
                    cumulative = s.bucket_counts[i]
                    base = tuple(zip(h.labelnames, (v for _, v in s.labels)))
                    # Prometheus convention: +Inf bucket is rendered with
                    # le="+Inf", not the Python "Inf" repr.
                    le = "+Inf" if bound == float("inf") else str(bound)
                    bucket_labels = base + (("le", le),)
                    labels_str = _format_labels(bucket_labels)
                    lines.append(f"{h.name}_bucket{labels_str} {cumulative}")
                # _count and _sum
                labels_str = _format_labels(s.labels)
                lines.append(f"{h.name}_count{labels_str} {s.count}")
                lines.append(f"{h.name}_sum{labels_str} {s.sum_ms}")
        return "\n".join(lines) + "\n"


def _format_labels(labels: Iterable[tuple[str, str]]) -> str:
    """Render labels as Prometheus ``{a="1",b="2"}`` or empty string."""
    pairs = [(k, _escape_label_value(v)) for k, v in labels]
    if not pairs:
        return ""
    return "{" + ",".join(f'{k}="{v}"' for k, v in pairs) + "}"


def _escape_label_value(v: str) -> str:
    """Escape per Prometheus exposition format (backslash, newline, quote)."""
    return v.replace("\\", "\\\\").replace('"', '\\"').replace("\n", "\\n")


_REGISTRY: _Registry | None = None
_REGISTRY_LOCK = threading.Lock()


def get_registry() -> _Registry:
    """Return the process-wide metrics registry (lazy singleton)."""
    global _REGISTRY
    if _REGISTRY is None:
        with _REGISTRY_LOCK:
            if _REGISTRY is None:
                _REGISTRY = _Registry()
    return _REGISTRY


# ---------- default browser metrics ----------

# These are pre-registered so callsites can just import and use them.
browser_check_total = get_registry().register_counter(
    Counter(
        "browser_check_total",
        "Total browser_check invocations, partitioned by outcome.",
        labelnames=("outcome",),
    )
)

browser_check_duration_ms = get_registry().register_histogram(
    Histogram(
        "browser_check_duration_ms",
        "Browser check wall-clock duration in milliseconds.",
        labelnames=("engine",),
    )
)

circuit_state_transitions_total = get_registry().register_counter(
    Counter(
        "browser_circuit_state_transitions_total",
        "Total circuit-breaker state transitions, partitioned by from/to state.",
        labelnames=("from_state", "to_state"),
    )
)

circuit_open_count = get_registry().register_gauge(
    Gauge(
        "browser_circuit_open_count",
        "Number of per-thread circuit breakers currently in OPEN state.",
    )
)

retry_attempts_total = get_registry().register_counter(
    Counter(
        "browser_retry_attempts_total",
        "Total retry attempts, partitioned by outcome (succeeded, exhausted, gave_up).",
        labelnames=("outcome",),
    )
)

screenshot_total = get_registry().register_counter(
    Counter(
        "browser_screenshot_total",
        "Total screenshot tool invocations, partitioned by outcome.",
        labelnames=("outcome",),
    )
)


def render_metrics() -> str:
    """Render the full registry as Prometheus text format."""
    return get_registry().render_prometheus()