"""Privacy audit trail for iGIN0 search operations.

Logs structured JSON records of every search and fetch operation for
compliance and debugging. Records include query, sources, outcomes,
privacy mode, TOR usage, and timing.

Configuration (env vars):
    DEERFLOW_IGINO_AUDIT_ENABLED     default true
    DEERFLOW_IGINO_AUDIT_REDACT      default false (redact queries)
"""

from __future__ import annotations

import json
import logging
import os
import threading
import uuid
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from typing import Any

logger = logging.getLogger(__name__)

_ENABLED = os.environ.get("DEERFLOW_IGINO_AUDIT_ENABLED", "true").lower() in ("true", "1", "yes")
_REDACT = os.environ.get("DEERFLOW_IGINO_AUDIT_REDACT", "false").lower() in ("true", "1", "yes")


@dataclass
class AuditRecord:
    audit_id: str = field(default_factory=lambda: uuid.uuid4().hex[:16])
    timestamp: str = field(default_factory=lambda: datetime.now(UTC).isoformat())
    thread_id: str = ""
    query: str = ""
    privacy_mode: bool = False
    tor_used: bool = False
    sources_searched: list[str] = field(default_factory=list)
    sources_fetched: list[str] = field(default_factory=list)
    results_returned: int = 0
    results_succeeded: int = 0
    duration_ms: float = 0.0
    cache_hit: bool = False
    error: str | None = None
    compliance_tags: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        if _REDACT and d.get("query"):
            d["query"] = "[REDACTED]"
        return d

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), ensure_ascii=False)


class AuditTrail:
    """In-memory audit trail with optional persistence."""

    def __init__(self, enabled: bool = _ENABLED) -> None:
        self._enabled = enabled
        self._records: list[AuditRecord] = []
        self._lock = threading.Lock()
        self._max_records = 10000

    def record(self, rec: AuditRecord) -> None:
        if not self._enabled:
            return
        with self._lock:
            if len(self._records) >= self._max_records:
                self._records = self._records[-self._max_records // 2 :]
            self._records.append(rec)
        logger.info("iGIN0 audit: %s", rec.to_json())

    def search(
        self,
        *,
        thread_id: str = "",
        query: str = "",
        privacy_mode: bool = False,
        tor_used: bool = False,
        sources_searched: list[str] | None = None,
        results_returned: int = 0,
        duration_ms: float = 0.0,
        cache_hit: bool = False,
        error: str | None = None,
    ) -> AuditRecord:
        rec = AuditRecord(
            thread_id=thread_id,
            query=query,
            privacy_mode=privacy_mode,
            tor_used=tor_used,
            sources_searched=sources_searched or [],
            results_returned=results_returned,
            duration_ms=duration_ms,
            cache_hit=cache_hit,
            error=error,
            compliance_tags=["search"],
        )
        self.record(rec)
        return rec

    def fetch(
        self,
        *,
        thread_id: str = "",
        url: str = "",
        source: str = "",
        privacy_mode: bool = False,
        tor_used: bool = False,
        success: bool = True,
        duration_ms: float = 0.0,
        error: str | None = None,
    ) -> AuditRecord:
        rec = AuditRecord(
            thread_id=thread_id,
            query=url,
            privacy_mode=privacy_mode,
            tor_used=tor_used,
            sources_fetched=[source] if source else [],
            results_succeeded=1 if success else 0,
            duration_ms=duration_ms,
            error=error,
            compliance_tags=["fetch"],
        )
        self.record(rec)
        return rec

    def get_records(self, limit: int = 100) -> list[dict[str, Any]]:
        with self._lock:
            return [r.to_dict() for r in self._records[-limit:]]

    def get_stats(self) -> dict[str, Any]:
        with self._lock:
            total = len(self._records)
            errors = sum(1 for r in self._records if r.error)
            tor_count = sum(1 for r in self._records if r.tor_used)
            return {
                "total_records": total,
                "errors": errors,
                "tor_usage": tor_count,
                "enabled": self._enabled,
                "redacted": _REDACT,
            }

    def clear(self) -> None:
        with self._lock:
            self._records.clear()


_trail: AuditTrail | None = None
_trail_lock = threading.Lock()


def get_audit_trail() -> AuditTrail:
    global _trail
    if _trail is not None:
        return _trail
    with _trail_lock:
        if _trail is None:
            _trail = AuditTrail()
        return _trail


def reset_audit_trail() -> None:
    global _trail
    with _trail_lock:
        if _trail is not None:
            _trail.clear()
        _trail = None
