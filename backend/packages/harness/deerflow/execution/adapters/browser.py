"""Browser adapter — audited CDP session acquisition through the kernel.

Phase C7 — the repository audit found no browser *process launches*:
every browser interaction connects to an existing chromium over CDP
(the AIO sandbox container owns the browser process).  This adapter
therefore models browser work as **session acquisition**, not spawn:

- policy-gates and audits every CDP connection like an execution,
- wraps ``playwright.sync_api.connect_over_cdp`` behind one choke point,
- records session open/close in the kernel's audit trail so browser
  activity is visible next to process executions.

Playwright is imported lazily — environments without it can still import
this module.
"""

from __future__ import annotations

import contextlib
import logging
import time
from collections.abc import Iterator
from datetime import UTC, datetime

from deerflow.execution.adapters.base import BaseAdapter
from deerflow.execution.models import (
    now_iso,
    ExecutionClass,
    ExecutionRequest,
    ExecutionResult,
    ExecutionStatus,
    ResourceLimits,
)

logger = logging.getLogger(__name__)


class BrowserAdapter(BaseAdapter):
    """Acquire audited CDP sessions against a running chromium."""

    execution_class = ExecutionClass.BROWSER

    @contextlib.contextmanager
    def session(
        self,
        cdp_url: str,
        *,
        timeout_ms: float = 15000,
        intent: str = "",
        correlation_id: str = "",
        thread_id: str = "",
    ) -> Iterator[object]:
        """Context manager yielding a connected playwright Browser.

        The connection attempt and outcome are recorded in the kernel's
        audit trail as a BROWSER-class execution (argv is descriptive —
        no process is created).
        """
        request = ExecutionRequest(
            argv=("cdp-connect", cdp_url),
            execution_class=ExecutionClass.BROWSER,
            limits=ResourceLimits(timeout=timeout_ms / 1000.0),
            intent=intent or f"CDP session {cdp_url}",
            correlation_id=correlation_id,
            thread_id=thread_id,
        )
        started = time.monotonic()
        started_at = now_iso()
        status = ExecutionStatus.FAILED
        error: str | None = None
        try:
            from playwright.sync_api import sync_playwright

            with sync_playwright() as pw:
                browser = pw.chromium.connect_over_cdp(cdp_url, timeout=timeout_ms)
                status = ExecutionStatus.SUCCEEDED
                try:
                    yield browser
                finally:
                    with contextlib.suppress(Exception):
                        browser.close()
        except GeneratorExit:
            raise
        except Exception as e:
            error = f"{type(e).__name__}: {e}"
            raise
        finally:
            duration_ms = (time.monotonic() - started) * 1000.0
            result = ExecutionResult(
                execution_id=request.execution_id,
                status=status,
                exit_code=0 if status is ExecutionStatus.SUCCEEDED else None,
                duration_ms=duration_ms,
                started_at=started_at,
                finished_at=now_iso(),
                error=error,
                execution_class=ExecutionClass.BROWSER,
                correlation_id=correlation_id,
            )
            audit = getattr(self._kernel, "audit_engine", None)
            metrics = getattr(self._kernel, "metrics", None)
            if audit is not None:
                audit.record(request, result)
            if metrics is not None:
                metrics.observe(ExecutionClass.BROWSER, status, duration_ms)
