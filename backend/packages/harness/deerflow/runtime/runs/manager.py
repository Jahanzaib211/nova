"""In-memory run registry with optional persistent RunStore backing."""

from __future__ import annotations

import asyncio
import logging
import sqlite3
import uuid
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from deerflow.runtime.stream_bridge.diagnostics import register_correlation_id
from deerflow.utils.time import now_iso as _now_iso

from .cancel_signal import CancelSignal, NoopCancelSignal
from .distributed_lock import DistributedLock, NoopDistributedLock
from .schemas import DisconnectMode, RunStatus

if TYPE_CHECKING:
    from deerflow.execution.supervisor import Supervisor
    from deerflow.runtime.runs.store.base import RunStore

logger = logging.getLogger(__name__)

_RETRYABLE_SQLITE_MESSAGES = (
    "database is locked",
    "database table is locked",
    "database is busy",
)

_RETRYABLE_SQLITE_ERROR_CODES = {
    sqlite3.SQLITE_BUSY,
    sqlite3.SQLITE_LOCKED,
}


def _is_retryable_persistence_error(exc: BaseException) -> bool:
    """Return True for transient SQLite persistence failures.

    SQLite lock contention normally surfaces through either sqlite3 exceptions
    or SQLAlchemy wrappers.  The short bounded retry here protects run status
    finalization from transient writer pressure without hiding permanent
    failures forever.
    """

    pending: list[BaseException] = [exc]
    seen: set[int] = set()
    while pending:
        current = pending.pop()
        if id(current) in seen:
            continue
        seen.add(id(current))

        message = str(current).lower()
        if any(fragment in message for fragment in _RETRYABLE_SQLITE_MESSAGES):
            return True
        if isinstance(current, (sqlite3.OperationalError, sqlite3.DatabaseError)):
            error_code = getattr(current, "sqlite_errorcode", None)
            if error_code in _RETRYABLE_SQLITE_ERROR_CODES:
                return True
        for chained in (getattr(current, "orig", None), current.__cause__, current.__context__):
            if isinstance(chained, BaseException):
                pending.append(chained)
    return False


@dataclass(frozen=True)
class PersistenceRetryPolicy:
    """Bounded retry policy for short run-store writes."""

    max_attempts: int = 5
    initial_delay: float = 0.05
    max_delay: float = 1.0
    backoff_factor: float = 2.0


@dataclass
class RunRecord:
    """Mutable record for a single run."""

    run_id: str
    thread_id: str
    assistant_id: str | None
    status: RunStatus
    on_disconnect: DisconnectMode
    multitask_strategy: str = "reject"
    metadata: dict = field(default_factory=dict)
    kwargs: dict = field(default_factory=dict)
    user_id: str | None = None
    created_at: str = ""
    updated_at: str = ""
    task: asyncio.Task | None = field(default=None, repr=False)
    abort_event: asyncio.Event = field(default_factory=asyncio.Event, repr=False)
    abort_action: str = "interrupt"
    error: str | None = None
    model_name: str | None = None
    store_only: bool = False
    total_input_tokens: int = 0
    total_output_tokens: int = 0
    total_tokens: int = 0
    llm_call_count: int = 0
    lead_agent_tokens: int = 0
    subagent_tokens: int = 0
    middleware_tokens: int = 0
    # Per-model token breakdown
    token_usage_by_model: dict[str, dict[str, int]] = field(default_factory=dict)
    message_count: int = 0
    last_ai_message: str | None = None
    first_human_message: str | None = None
    # Phase C0 — cross-process correlation identifier.
    # Stable across backend → SSE → frontend → recovery logs. Generated
    # exactly once per run at create() / create_or_reject() time. Defaults
    # to "" for legacy rows hydrated from a store written before this field
    # existed; consumers must treat "" as "unknown, fall back to run_id".
    correlation_id: str = ""


class RunManager:
    """In-memory run registry with optional persistent RunStore backing.

    All mutations are protected by an asyncio lock. When a ``store`` is
    provided, serializable metadata is also persisted to the store so
    that run history survives process restarts.

    Phase C8: optionally accepts a Supervisor reference for two-phase
    cancellation. When a run is cancelled, both the asyncio task AND
    any running kernel processes are terminated.
    """

    def __init__(
        self,
        store: RunStore | None = None,
        *,
        persistence_retry_policy: PersistenceRetryPolicy | None = None,
        supervisor: Supervisor | None = None,
        cancel_signal: CancelSignal | None = None,
        distributed_lock: DistributedLock | None = None,
    ) -> None:
        self._runs: dict[str, RunRecord] = {}
        # Secondary index: thread_id -> insertion-ordered run_id set (a dict is
        # used as an ordered set), maintained in lockstep with ``_runs`` so
        # per-thread queries avoid O(total in-memory runs) full scans while
        # preserving ``_runs`` iteration order (see ``_thread_records_locked``).
        self._runs_by_thread: dict[str, dict[str, None]] = {}
        self._lock = asyncio.Lock()
        self._store = store
        self._persistence_retry_policy = persistence_retry_policy or PersistenceRetryPolicy()
        # Phase C8: Supervisor for two-phase cancellation
        self._supervisor = supervisor
        # Cross-replica cancel notification — see cancel_signal.py. Defaults
        # to a no-op, matching today's single-replica-only behavior exactly
        # when no Redis is configured.
        self._cancel_signal: CancelSignal = cancel_signal or NoopCancelSignal()
        # Cross-replica mutual exclusion for create_or_reject()'s "reject"
        # strategy — see distributed_lock.py. Defaults to a no-op.
        self._distributed_lock: DistributedLock = distributed_lock or NoopDistributedLock()

    def _index_run_locked(self, record: RunRecord) -> None:
        """Register *record* in the thread index. Caller must hold ``self._lock``."""
        self._runs_by_thread.setdefault(record.thread_id, {})[record.run_id] = None

    def _unindex_run_locked(self, run_id: str, thread_id: str) -> None:
        """Drop *run_id* from the thread index. Caller must hold ``self._lock``."""
        bucket = self._runs_by_thread.get(thread_id)
        if bucket is not None:
            bucket.pop(run_id, None)
            if not bucket:
                self._runs_by_thread.pop(thread_id, None)

    def _thread_records_locked(self, thread_id: str) -> list[RunRecord]:
        """Return live in-memory records for *thread_id*. Caller must hold ``self._lock``.

        Uses the ``_runs_by_thread`` index for O(runs-in-thread) lookup instead of
        scanning every in-memory run. Correctness rests on the index and ``_runs``
        being mutated in lockstep under ``self._lock`` (no ``await`` between the two
        writes), so any holder of the lock sees them agree. The ``self._runs.get``
        filter is defense-in-depth, not reconciliation: it drops a stale id still in
        the index but already gone from ``_runs``, yet it cannot recover a run that is
        in ``_runs`` but missing from the index (such a run would be silently
        omitted). It guards only that one direction, should a future refactor ever
        break the lockstep invariant.
        """
        run_ids = self._runs_by_thread.get(thread_id)
        if not run_ids:
            return []
        return [record for run_id in run_ids if (record := self._runs.get(run_id)) is not None]

    @staticmethod
    def _store_put_payload(record: RunRecord, *, error: str | None = None) -> dict[str, Any]:
        payload = {
            "thread_id": record.thread_id,
            "assistant_id": record.assistant_id,
            "status": record.status.value,
            "multitask_strategy": record.multitask_strategy,
            "metadata": record.metadata or {},
            "kwargs": record.kwargs or {},
            "error": error if error is not None else record.error,
            "created_at": record.created_at,
            "model_name": record.model_name,
        }
        if record.user_id is not None:
            payload["user_id"] = record.user_id
        if record.correlation_id:
            payload["correlation_id"] = record.correlation_id
        return payload

    async def _call_store_with_retry(
        self,
        operation_name: str,
        run_id: str,
        operation: Callable[[], Awaitable[Any]],
    ) -> Any:
        """Run a short store operation with bounded retries for SQLite pressure."""
        policy = self._persistence_retry_policy
        attempt = 1
        delay = policy.initial_delay
        while True:
            try:
                return await operation()
            except Exception as exc:
                retryable = _is_retryable_persistence_error(exc)
                if attempt >= policy.max_attempts or not retryable:
                    raise
                logger.warning(
                    "Transient persistence failure during %s for run %s (attempt %d/%d); retrying",
                    operation_name,
                    run_id,
                    attempt,
                    policy.max_attempts,
                    exc_info=True,
                )
                if delay > 0:
                    await asyncio.sleep(delay)
                delay = min(policy.max_delay, delay * policy.backoff_factor if delay else policy.initial_delay)
                attempt += 1

    async def _persist_snapshot_to_store(self, run_id: str, payload: dict[str, Any]) -> bool:
        """Best-effort persist a previously captured run snapshot."""
        if self._store is None:
            return True
        try:
            await self._call_store_with_retry(
                "put",
                run_id,
                lambda: self._store.put(run_id, **payload),
            )
            return True
        except Exception:
            logger.warning("Failed to persist run %s to store", run_id, exc_info=True)
            return False

    async def _persist_new_run_to_store(self, record: RunRecord) -> None:
        """Persist a newly created run record to the backing store.

        Initial run creation is part of the run visibility boundary: callers
        should not observe a run in memory unless its backing store row exists.
        Unlike follow-up status/model updates, failures are propagated so the
        caller can treat creation as failed. Rollback is the caller's
        responsibility after inserting the record into ``_runs``.
        """
        if self._store is None:
            return
        await self._call_store_with_retry(
            "put",
            record.run_id,
            lambda: self._store.put(record.run_id, **self._store_put_payload(record)),
        )

    async def _persist_to_store(self, record: RunRecord, *, error: str | None = None) -> bool:
        """Best-effort persist run record to backing store."""
        return await self._persist_snapshot_to_store(
            record.run_id,
            self._store_put_payload(record, error=error),
        )

    async def _persist_status(self, record: RunRecord, status: RunStatus, *, error: str | None = None) -> bool:
        """Best-effort persist a status transition to the backing store."""
        if self._store is None:
            return True
        row_recovery_payload = self._store_put_payload(record, error=error)
        try:
            updated = await self._call_store_with_retry(
                "update_status",
                record.run_id,
                lambda: self._store.update_status(record.run_id, status.value, error=error),
            )
            if updated is False:
                return await self._persist_snapshot_to_store(record.run_id, row_recovery_payload)
            return True
        except Exception:
            logger.warning("Failed to persist status update for run %s", record.run_id, exc_info=True)
            return False

    @staticmethod
    def _record_from_store(row: dict[str, Any]) -> RunRecord:
        """Build a read-only runtime record from a serialized store row.

        NULL status/on_disconnect columns (e.g. from rows written before those
        columns were added) default to ``pending`` and ``cancel`` respectively.
        """
        return RunRecord(
            run_id=row["run_id"],
            thread_id=row["thread_id"],
            assistant_id=row.get("assistant_id"),
            status=RunStatus(row.get("status") or RunStatus.pending.value),
            on_disconnect=DisconnectMode(row.get("on_disconnect") or DisconnectMode.cancel.value),
            multitask_strategy=row.get("multitask_strategy") or "reject",
            metadata=row.get("metadata") or {},
            kwargs=row.get("kwargs") or {},
            created_at=row.get("created_at") or "",
            updated_at=row.get("updated_at") or "",
            user_id=row.get("user_id"),
            error=row.get("error"),
            model_name=row.get("model_name"),
            store_only=True,
            total_input_tokens=row.get("total_input_tokens") or 0,
            total_output_tokens=row.get("total_output_tokens") or 0,
            total_tokens=row.get("total_tokens") or 0,
            llm_call_count=row.get("llm_call_count") or 0,
            lead_agent_tokens=row.get("lead_agent_tokens") or 0,
            subagent_tokens=row.get("subagent_tokens") or 0,
            middleware_tokens=row.get("middleware_tokens") or 0,
            token_usage_by_model=row.get("token_usage_by_model") or {},
            message_count=row.get("message_count") or 0,
            last_ai_message=row.get("last_ai_message"),
            first_human_message=row.get("first_human_message"),
            correlation_id=row.get("correlation_id") or "",
        )

    async def update_run_completion(self, run_id: str, **kwargs) -> None:
        """Persist token usage and completion data to the backing store."""
        row_recovery_payload: dict[str, Any] | None = None
        async with self._lock:
            record = self._runs.get(run_id)
            if record is not None:
                for key, value in kwargs.items():
                    if key == "status":
                        continue
                    if hasattr(record, key) and value is not None:
                        setattr(record, key, value)
                record.updated_at = _now_iso()
                row_recovery_payload = self._store_put_payload(record, error=kwargs.get("error"))
        if self._store is None:
            return
        try:
            updated = await self._call_store_with_retry(
                "update_run_completion",
                run_id,
                lambda: self._store.update_run_completion(run_id, **kwargs),
            )
            if updated is False:
                if row_recovery_payload is None:
                    logger.warning("Failed to recreate missing run %s for completion persistence", run_id)
                    return
                if not await self._persist_snapshot_to_store(run_id, row_recovery_payload):
                    return
                recovered = await self._call_store_with_retry(
                    "update_run_completion",
                    run_id,
                    lambda: self._store.update_run_completion(run_id, **kwargs),
                )
                if recovered is False:
                    logger.warning("Run completion update for %s affected no rows after row recreation", run_id)
        except Exception:
            logger.warning("Failed to persist run completion for %s", run_id, exc_info=True)

    async def update_run_progress(self, run_id: str, **kwargs) -> None:
        """Persist a running token/message snapshot without changing status."""
        should_persist = True
        async with self._lock:
            record = self._runs.get(run_id)
            if record is not None:
                should_persist = record.status == RunStatus.running
            if record is not None and should_persist:
                for key, value in kwargs.items():
                    if hasattr(record, key) and value is not None:
                        setattr(record, key, value)
                record.updated_at = _now_iso()
        if should_persist and self._store is not None:
            try:
                await self._store.update_run_progress(run_id, **kwargs)
            except Exception:
                logger.warning("Failed to persist run progress for %s", run_id, exc_info=True)

    async def create(
        self,
        thread_id: str,
        assistant_id: str | None = None,
        *,
        on_disconnect: DisconnectMode = DisconnectMode.cancel,
        metadata: dict | None = None,
        kwargs: dict | None = None,
        multitask_strategy: str = "reject",
        user_id: str | None = None,
    ) -> RunRecord:
        """Create a new pending run and register it."""
        run_id = str(uuid.uuid4())
        correlation_id = uuid.uuid4().hex
        now = _now_iso()
        record = RunRecord(
            run_id=run_id,
            thread_id=thread_id,
            assistant_id=assistant_id,
            status=RunStatus.pending,
            on_disconnect=on_disconnect,
            multitask_strategy=multitask_strategy,
            metadata=metadata or {},
            kwargs=kwargs or {},
            user_id=user_id,
            created_at=now,
            updated_at=now,
            correlation_id=correlation_id,
        )
        async with self._lock:
            self._runs[run_id] = record
            self._index_run_locked(record)
            # Phase C0 — register the run's correlation_id so every
            # subsequent diagnostics record stamped for this run or
            # thread automatically carries the canonical identifier.
            register_correlation_id(
                run_id=run_id,
                thread_id=thread_id,
                correlation_id=correlation_id,
            )
            persisted = False
            try:
                await self._persist_new_run_to_store(record)
                persisted = True
            except Exception:
                logger.warning("Failed to persist run %s; rolled back in-memory record", run_id, exc_info=True)
                raise
            finally:
                # Also covers cancellation, which bypasses ``except Exception``.
                if not persisted:
                    self._runs.pop(run_id, None)
                    self._unindex_run_locked(run_id, record.thread_id)
        logger.info("Run created: run_id=%s thread_id=%s", run_id, thread_id)
        return record

    async def get(self, run_id: str, *, user_id: str | None = None) -> RunRecord | None:
        """Return a run record by ID, or ``None``.

        Args:
            run_id: The run ID to look up.
            user_id: Optional user ID for permission filtering when hydrating from store.
        """
        async with self._lock:
            record = self._runs.get(run_id)
        if record is not None:
            return record
        if self._store is None:
            return None
        try:
            row = await self._store.get(run_id, user_id=user_id)
        except Exception:
            logger.warning("Failed to hydrate run %s from store", run_id, exc_info=True)
            return None
        # Re-check after store await: a concurrent create() may have inserted the
        # in-memory record while the store call was in flight.
        async with self._lock:
            record = self._runs.get(run_id)
        if record is not None:
            return record
        if row is None:
            return None
        try:
            return self._record_from_store(row)
        except Exception:
            logger.warning("Failed to map store row for run %s", run_id, exc_info=True)
            return None

    async def aget(self, run_id: str, *, user_id: str | None = None) -> RunRecord | None:
        """Return a run record by ID, checking the persistent store as fallback.

        Alias for :meth:`get` for backward compatibility.
        """
        return await self.get(run_id, user_id=user_id)

    async def list_by_thread(self, thread_id: str, *, user_id: str | None = None, limit: int = 100) -> list[RunRecord]:
        """Return runs for a given thread, newest first, at most ``limit`` records.

        In-memory runs take precedence only when the same ``run_id`` exists in both
        memory and the backing store. The merged result is then sorted newest-first
        by ``created_at`` and trimmed to ``limit`` (default 100).

        Args:
            thread_id: The thread ID to filter by.
            user_id: Optional user ID for permission filtering when hydrating from store.
            limit: Maximum number of runs to return.
        """
        async with self._lock:
            memory_records = self._thread_records_locked(thread_id)
        if self._store is None:
            return sorted(memory_records, key=lambda r: r.created_at, reverse=True)[:limit]
        records_by_id = {record.run_id: record for record in memory_records}
        store_limit = max(0, limit - len(memory_records))
        try:
            rows = await self._store.list_by_thread(thread_id, user_id=user_id, limit=store_limit)
        except Exception:
            logger.warning("Failed to hydrate runs for thread %s from store", thread_id, exc_info=True)
            return sorted(memory_records, key=lambda r: r.created_at, reverse=True)[:limit]
        for row in rows:
            run_id = row.get("run_id")
            if run_id and run_id not in records_by_id:
                try:
                    record = self._record_from_store(row)
                    records_by_id[run_id] = record
                    # Phase C0 — rehydrate the correlation registry for
                    # store-only rows so subsequent diagnostics carry
                    # the original identifier (e.g. after a worker
                    # restart, the next request still gets correlation-
                    # stamped records).
                    cid = row.get("correlation_id")
                    if cid:
                        register_correlation_id(
                            run_id=run_id,
                            thread_id=row.get("thread_id"),
                            correlation_id=cid,
                        )
                except Exception:
                    logger.warning("Failed to map store row for run %s", run_id, exc_info=True)
        return sorted(records_by_id.values(), key=lambda record: record.created_at, reverse=True)[:limit]

    async def set_status(self, run_id: str, status: RunStatus, *, error: str | None = None) -> None:
        """Transition a run to a new status."""
        async with self._lock:
            record = self._runs.get(run_id)
            if record is None:
                logger.warning("set_status called for unknown run %s", run_id)
                return
            record.status = status
            record.updated_at = _now_iso()
            if error is not None:
                record.error = error
        await self._persist_status(record, status, error=error)
        logger.info("Run %s -> %s", run_id, status.value)

    async def _persist_model_name(self, run_id: str, model_name: str | None) -> None:
        """Best-effort persist model_name update to the backing store."""
        if self._store is None:
            return
        try:
            await self._call_store_with_retry(
                "update_model_name",
                run_id,
                lambda: self._store.update_model_name(run_id, model_name),
            )
        except Exception:
            logger.warning("Failed to persist model_name update for run %s", run_id, exc_info=True)

    async def update_model_name(self, run_id: str, model_name: str | None) -> None:
        """Update the model name for a run."""
        async with self._lock:
            record = self._runs.get(run_id)
            if record is None:
                logger.warning("update_model_name called for unknown run %s", run_id)
                return
            record.model_name = model_name
            record.updated_at = _now_iso()
        await self._persist_model_name(run_id, model_name)
        logger.info("Run %s model_name=%s", run_id, model_name)

    async def cancel(self, run_id: str, *, action: str = "interrupt") -> bool:
        """Request cancellation of a run.

        Args:
            run_id: The run ID to cancel.
            action: "interrupt" keeps checkpoint, "rollback" reverts to pre-run state.

        Two-phase cancellation (Phase C8):
        1. Phase 1: Sets abort event and cancels asyncio task (returns immediately).
        2. Phase 2: Kernel cancels OS process via supervisor (async, fire-and-forget).

        Returns ``True`` if cancellation was initiated **or** the run was already
        interrupted (idempotent — a second cancel is a no-op success).
        Returns ``False`` only when the run is unknown to this worker or has
        reached a terminal state other than interrupted (completed, failed, etc.).

        Phase 6: when the run is unknown in-memory but the store has a
        non-terminal row (store-only hydration, e.g. after a worker
        restart), persist ``interrupted`` through the store and return
        ``True``. The user Stop button must always land, never 409
        forever on a "not active on this worker" row.
        """
        # Phase 1: Acquire lock and handle in-memory record
        async with self._lock:
            record = self._runs.get(run_id)
            if record is not None:
                if record.status == RunStatus.interrupted:
                    self._schedule_kernel_cancel(run_id)
                    return True  # idempotent — already cancelled on this worker
                if record.status not in (RunStatus.pending, RunStatus.running):
                    return False
                record.abort_action = action
                record.abort_event.set()
                if record.task is not None and not record.task.done():
                    record.task.cancel()
                record.status = RunStatus.interrupted
                record.updated_at = _now_iso()
            else:
                record = None

        # Phase 1 continued: persist and return
        if record is not None:
            await self._persist_status(record, RunStatus.interrupted)
            logger.info("Run %s cancelled (action=%s)", run_id, action)
            self._schedule_kernel_cancel(run_id)
            return True

        # Store-only path: run is not in memory, check store
        if self._store is None:
            return False
        try:
            row = await self._store.get(run_id)
        except Exception:
            logger.warning("Failed to read store row for cancel %s", run_id, exc_info=True)
            return False
        if row is None:
            return False
        status_value = row.get("status")
        if status_value == RunStatus.interrupted.value:
            self._schedule_kernel_cancel(run_id)
            # Re-publish on a repeat cancel too — pub/sub delivery isn't
            # guaranteed (a message published while the owning replica's
            # listener wasn't yet subscribed, e.g. a race at run startup,
            # is simply lost, not queued), so a second "stop" click is a
            # cheap, harmless way to retry the signal.
            await self._cancel_signal.request_cancel(run_id)
            return True  # idempotent
        if status_value not in (RunStatus.pending.value, RunStatus.running.value):
            return False
        try:
            await self._call_store_with_retry(
                "update_status",
                run_id,
                lambda: self._store.update_status(
                    run_id,
                    RunStatus.interrupted.value,
                    error=f"cancelled via REST (action={action})",
                ),
            )
        except Exception:
            logger.warning("Failed to persist interrupted for store-only run %s", run_id, exc_info=True)
            return False
        self._schedule_kernel_cancel(run_id)
        # This run isn't in self._runs on THIS process — either it's owned
        # by a different replica (the actual multi-replica case this
        # exists for) or this worker restarted and the owning process is
        # simply gone (in which case nothing is listening and this is a
        # harmless no-op). Either way, the store write above is the
        # durable signal; this is the low-latency wake-up for the common
        # case where the owner is alive on another replica.
        await self._cancel_signal.request_cancel(run_id)
        logger.info("Store-only run %s marked interrupted (action=%s)", run_id, action)
        return True

    async def _watch_remote_cancel(self, run_id: str, abort_event: asyncio.Event) -> None:
        try:
            await self._cancel_signal.wait_for_cancel(run_id)
            abort_event.set()
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.warning("cancel_signal.wait_for_cancel failed for run %s", run_id, exc_info=True)

    def start_remote_cancel_watcher(self, run_id: str, abort_event: asyncio.Event) -> asyncio.Task:
        """Spawn a background task that sets *abort_event* when a cancel
        request for *run_id* arrives from another replica (see
        cancel_signal.py). No-op-forever with the default NoopCancelSignal.

        Callers (``services.py``'s ``start_run``) must cancel the returned
        task once the run's own task completes, to avoid leaking a
        subscription per finished run.
        """
        return asyncio.create_task(self._watch_remote_cancel(run_id, abort_event))

    def _schedule_kernel_cancel(self, run_id: str) -> None:
        """Phase C8: schedule kernel cancel for a run if supervisor is wired.

        This is fire-and-forget — the kernel cancels asynchronously.
        """
        if self._supervisor is None:
            return
        execution_id = self._supervisor.get_execution_id(run_id)
        if not execution_id:
            return
        logger.info(
            "Run %s cancellation: kernel.cancel(execution_id=%s)",
            run_id,
            execution_id,
        )
        try:
            loop = asyncio.get_event_loop()
            loop.call_soon(lambda: self._supervisor.cancel(execution_id, grace_period=5.0))
        except Exception:
            pass

    async def reap_orphaned_runs(self) -> int:
        """Phase 6 startup reaper.

        On gateway boot the in-memory ``StreamBridge`` is empty: every run
        created by a previous process lives only in the store. Any row
        whose status is still ``pending`` or ``running`` is necessarily
        orphaned (the bridge that was executing it died with the process).
        Marking them ``interrupted`` converges the front-end polls to idle
        and prevents 409-on-Stop forever.

        Returns the number of rows reaped.
        """
        if self._store is None:
            return 0
        # We cannot enumerate every thread from here without a thread index,
        # but ``list_by_thread`` is the only public method on the store
        # interface. The bootstrap path is expected to call this once
        # AFTER threads are known, with a thread iterator — see
        # ``reap_orphaned_runs_for_threads``.
        return 0

    async def reap_orphaned_runs_for_threads(self, thread_ids: list[str]) -> int:
        """Phase 6 startup reaper — reaps orphaned runs across the given threads.

        Phase 6 fix: when the gateway boots, threads that were active on a
        previous worker are rehydrated from the store. Their runs with
        status ``pending`` or ``running`` are necessarily orphaned (the
        bridge died with the previous process). Mark them ``interrupted``
        so the front-end polls converge to idle and the user Stop button
        does not 409 forever.
        """
        if self._store is None:
            return 0
        reaped = 0
        active_statuses = {RunStatus.pending.value, RunStatus.running.value}
        terminal_statuses = {
            RunStatus.success.value,
            RunStatus.error.value,
            RunStatus.timeout.value,
            RunStatus.interrupted.value,
        }
        for thread_id in thread_ids:
            try:
                rows = await self._store.list_by_thread(thread_id, limit=1000)
            except Exception:
                logger.warning(
                    "Reaper: list_by_thread failed for %s",
                    thread_id,
                    exc_info=True,
                )
                continue
            for row in rows:
                run_id = row.get("run_id")
                status_value = row.get("status")
                if not run_id or status_value not in active_statuses:
                    continue
                try:
                    await self._call_store_with_retry(
                        "update_status",
                        run_id,
                        lambda: self._store.update_status(
                            run_id,
                            RunStatus.interrupted.value,
                            error="orphaned by gateway restart",
                        ),
                    )
                    reaped += 1
                except Exception:
                    logger.warning(
                        "Reaper: update_status failed for %s",
                        run_id,
                        exc_info=True,
                    )
        # Sanity: import-free reference so the variable is used (the active/
        # terminal sets are kept for readability / future extension).
        _ = active_statuses, terminal_statuses
        return reaped

    async def create_or_reject(
        self,
        thread_id: str,
        assistant_id: str | None = None,
        *,
        on_disconnect: DisconnectMode = DisconnectMode.cancel,
        metadata: dict | None = None,
        kwargs: dict | None = None,
        multitask_strategy: str = "reject",
        model_name: str | None = None,
        user_id: str | None = None,
    ) -> RunRecord:
        """Atomically check for inflight runs and create a new one.

        For ``reject`` strategy, raises ``ConflictError`` if thread
        already has a pending/running run.  For ``interrupt``/``rollback``,
        cancels inflight runs before creating.

        This method holds the lock across both the check and the insert,
        eliminating the TOCTOU race in separate ``has_inflight`` + ``create`` —
        but that local ``asyncio.Lock`` only protects this one process. With
        multiple Gateway replicas, two concurrent requests for the same
        thread hitting *different* replicas would each pass their own local
        check simultaneously. ``self._distributed_lock`` (see
        distributed_lock.py, a no-op when no Redis is configured) closes
        that gap by serializing this whole method per thread_id across
        replicas; the store-inflight check below additionally covers runs
        owned by *other* replicas, which this process's own ``_runs`` can't
        see at all.

        Scope note: the reject-strategy race is what this closes. The
        interrupt/rollback strategies still only cancel *locally-visible*
        inflight runs — cancelling a run owned by a different replica needs
        the same cross-replica cancel path ``RunManager.cancel()`` already
        uses (see cancel_signal.py), which create_or_reject doesn't invoke
        today. Documented as a known smaller residual gap, not silently
        assumed fixed.
        """
        run_id = str(uuid.uuid4())
        correlation_id = uuid.uuid4().hex
        now = _now_iso()

        _supported_strategies = ("reject", "interrupt", "rollback")
        interrupted_records: list[RunRecord] = []

        lock_key = f"thread:{thread_id}"
        if not await self._distributed_lock.acquire(lock_key, ttl_seconds=10):
            raise ConflictError(f"Thread {thread_id} already has a run being created on another replica")

        try:
            async with self._lock:
                if multitask_strategy not in _supported_strategies:
                    raise UnsupportedStrategyError(f"Multitask strategy '{multitask_strategy}' is not yet supported. Supported strategies: {', '.join(_supported_strategies)}")

                inflight = [r for r in self._thread_records_locked(thread_id) if r.status in (RunStatus.pending, RunStatus.running)]

                # Reap stale records whose tracked asyncio task has finished without a
                # clean status transition (e.g. the task died/was cancelled mid-run).
                # Without this, a dead run wedges the thread behind a permanent 409.
                # Records with no tracked task (task is None) are left alone: that
                # covers both brand-new runs still being set up by the caller and runs
                # whose task is tracked elsewhere.
                stale = [r for r in inflight if r.task is not None and r.task.done()]
                if stale:
                    for r in stale:
                        r.status = RunStatus.interrupted
                        r.updated_at = now
                        interrupted_records.append(r)
                        logger.info("Reaped stale inflight run %s on thread %s (dead task)", r.run_id, thread_id)
                    stale_ids = {r.run_id for r in stale}
                    inflight = [r for r in inflight if r.run_id not in stale_ids]

                if multitask_strategy == "reject" and inflight:
                    raise ConflictError(f"Thread {thread_id} already has an active run")

                if multitask_strategy == "reject" and self._store is not None:
                    # Local ``inflight`` only sees runs on THIS process. A run
                    # created by a different replica for the same thread is
                    # invisible to ``self._runs`` but very much real — check
                    # the durable, cross-replica-shared store too.
                    local_ids = {r.run_id for r in inflight}
                    try:
                        store_rows = await self._store.list_by_thread(thread_id, limit=20)
                    except Exception:
                        logger.warning("Failed to check store for cross-replica inflight runs on thread %s", thread_id, exc_info=True)
                        store_rows = []
                    remote_inflight = [
                        row
                        for row in store_rows
                        if row.get("status") in (RunStatus.pending.value, RunStatus.running.value) and row.get("run_id") not in local_ids
                    ]
                    if remote_inflight:
                        raise ConflictError(f"Thread {thread_id} already has an active run on another replica")

                if multitask_strategy in ("interrupt", "rollback") and inflight:
                    logger.info(
                        "Preparing to cancel %d inflight run(s) on thread %s (strategy=%s)",
                        len(inflight),
                        thread_id,
                        multitask_strategy,
                    )

                record = RunRecord(
                    run_id=run_id,
                    thread_id=thread_id,
                    assistant_id=assistant_id,
                    status=RunStatus.pending,
                    on_disconnect=on_disconnect,
                    multitask_strategy=multitask_strategy,
                    metadata=metadata or {},
                    kwargs=kwargs or {},
                    user_id=user_id,
                    created_at=now,
                    updated_at=now,
                    model_name=model_name,
                    correlation_id=correlation_id,
                )
                self._runs[run_id] = record
                self._index_run_locked(record)
                # Phase C0 — register the run's correlation_id so every
                # subsequent diagnostics record stamped for this run or
                # thread automatically carries the canonical identifier.
                register_correlation_id(
                    run_id=run_id,
                    thread_id=thread_id,
                    correlation_id=correlation_id,
                )
                persisted = False
                try:
                    await self._persist_new_run_to_store(record)
                    persisted = True
                except Exception:
                    logger.warning("Failed to persist run %s; rolled back in-memory record", run_id, exc_info=True)
                    raise
                finally:
                    # Also covers cancellation, which bypasses ``except Exception``.
                    if not persisted:
                        self._runs.pop(run_id, None)
                        self._unindex_run_locked(run_id, record.thread_id)

                if multitask_strategy in ("interrupt", "rollback") and inflight:
                    for r in inflight:
                        r.abort_action = multitask_strategy
                        r.abort_event.set()
                        if r.task is not None and not r.task.done():
                            r.task.cancel()
                        r.status = RunStatus.interrupted
                        r.updated_at = now
                        interrupted_records.append(r)
        finally:
            await self._distributed_lock.release(lock_key)

        for interrupted_record in interrupted_records:
            await self._persist_status(interrupted_record, RunStatus.interrupted)
        logger.info("Run created: run_id=%s thread_id=%s", run_id, thread_id)
        return record

    async def reconcile_orphaned_inflight_runs(
        self,
        *,
        error: str,
        before: str | None = None,
    ) -> list[RunRecord]:
        """Mark persisted active runs as failed when no local task owns them.

        Gateway runs are process-local: the asyncio task and abort event live in
        memory, while the run row is durable.  After a SQLite-backed gateway
        restart, any persisted ``pending`` or ``running`` row created before
        startup cannot still have a local worker.  This recovery step turns that
        ambiguous state into an explicit error instead of letting the UI show an
        indefinite active run.
        """
        if self._store is None:
            return []
        try:
            rows = await self._call_store_with_retry(
                "list_inflight",
                "*",
                lambda: self._store.list_inflight(before=before),
            )
        except Exception:
            logger.warning("Failed to list orphaned inflight runs for reconciliation", exc_info=True)
            return []

        recovered: list[RunRecord] = []
        now = _now_iso()
        for row in rows:
            try:
                record = self._record_from_store(row)
            except Exception:
                logger.warning("Failed to map orphaned run row during reconciliation", exc_info=True)
                continue

            async with self._lock:
                live_record = self._runs.get(record.run_id)
                if live_record is not None and live_record.status in (RunStatus.pending, RunStatus.running):
                    continue

            record.status = RunStatus.error
            record.error = error
            record.updated_at = now
            persisted = await self._persist_status(record, RunStatus.error, error=error)
            if not persisted:
                logger.warning("Skipped orphaned run %s recovery because error status was not persisted", record.run_id)
                continue
            recovered.append(record)

        if recovered:
            logger.warning("Recovered %d orphaned inflight run(s) as error", len(recovered))
        return recovered

    async def has_inflight(self, thread_id: str) -> bool:
        """Return ``True`` if *thread_id* has a pending or running run."""
        async with self._lock:
            return any(r.status in (RunStatus.pending, RunStatus.running) for r in self._thread_records_locked(thread_id))

    async def cleanup(self, run_id: str, *, delay: float = 300) -> None:
        """Remove a run record after an optional delay."""
        if delay > 0:
            await asyncio.sleep(delay)
        async with self._lock:
            record = self._runs.pop(run_id, None)
            if record is not None:
                self._unindex_run_locked(run_id, record.thread_id)
        logger.debug("Run record %s cleaned up", run_id)

    async def shutdown(self, *, timeout: float = 5.0) -> None:
        """Cancel and bounded-await all in-flight runs on process shutdown.

        Chat runs execute in fire-and-forget background ``asyncio`` tasks that
        write checkpoints through a shared checkpointer. On shutdown the
        checkpointer's resources (e.g. the postgres connection pool owned by the
        gateway's ``AsyncExitStack``) are torn down; if a run task is still
        mid-graph at that point, langgraph's
        ``AsyncPregelLoop._checkpointer_put_after_previous`` runs its
        ``finally: await checkpointer.aput(...)`` against the closed pool. Because
        that put runs in a langgraph-internal task (not on ``run_agent``'s call
        stack), the resulting ``psycopg_pool.PoolClosed`` is not catchable by the
        worker and surfaces as an unhandled exception during ``asyncio.run()``
        shutdown (bytedance/deer-flow issue #3373).

        Draining in-flight runs *before* the checkpointer is closed lets each
        run that settles within ``timeout`` flush its final checkpoint while
        resources are still open. Only runs that do **not** settle on their own
        are marked ``interrupted`` — a run that completes (e.g. ``success``)
        during the drain keeps its real terminal status instead of being
        blanket-overwritten. The whole drain, including the trailing status
        persistence, is bounded by ``timeout`` so a run stuck in cleanup (or a
        slow store under DB pressure) cannot hang worker shutdown — the
        precondition for the signal-reentrancy deadlock guarded by
        ``app.gateway.app._SHUTDOWN_HOOK_TIMEOUT_SECONDS``. Runs still active
        after ``timeout`` are logged and may still race teardown.
        """
        loop = asyncio.get_running_loop()
        deadline = loop.time() + timeout

        async with self._lock:
            inflight = [record for record in self._runs.values() if record.status in (RunStatus.pending, RunStatus.running) and record.task is not None and not record.task.done()]
            for record in inflight:
                record.abort_action = "interrupt"
                record.abort_event.set()
                record.task.cancel()  # type: ignore[union-attr]  # filtered above
                # Status is decided AFTER the drain (below), not here: a run that
                # completes on its own during the drain must keep its real status.

        if not inflight:
            return

        tasks = [record.task for record in inflight]
        _, pending = await asyncio.wait(tasks, timeout=timeout)

        # Only mark/persist ``interrupted`` for runs that did not settle on their
        # own (still pending after the timeout, or ended cancelled). A run that
        # finished normally during the drain keeps the status it set for itself.
        to_persist: list[RunRecord] = []
        async with self._lock:
            for record in inflight:
                task = record.task
                if task not in pending and not task.cancelled():
                    # Completed on its own — retrieve any surfaced exception so it
                    # is not reported as "never retrieved", and keep its status.
                    task.exception()  # type: ignore[union-attr]  # done & not cancelled
                    continue
                if record.status in (RunStatus.pending, RunStatus.running):
                    record.status = RunStatus.interrupted
                    record.updated_at = _now_iso()
                to_persist.append(record)

        # Bound the trailing status persistence within the remaining budget so a
        # slow store (``_call_store_with_retry`` can back off under DB pressure)
        # cannot push shutdown past ``timeout``.
        if to_persist:
            remaining = deadline - loop.time()
            if remaining <= 0:
                logger.warning("Run drain budget exhausted before persisting %d interrupted run(s) on shutdown", len(to_persist))
            else:
                try:
                    results = await asyncio.wait_for(
                        asyncio.gather(*(self._persist_status(record, RunStatus.interrupted) for record in to_persist), return_exceptions=True),
                        timeout=remaining,
                    )
                except TimeoutError:
                    logger.warning("Run drain status persistence exceeded the %.1fs budget; %d record(s) may not be persisted", timeout, len(to_persist))
                else:
                    # ``_persist_status`` is best-effort: it catches and logs its
                    # own failures, returning ``False``. Inspect the aggregate so a
                    # partial failure is surfaced at shutdown level (with the
                    # run_id) instead of being silently swallowed by the gather.
                    for record, result in zip(to_persist, results):
                        if isinstance(result, Exception):
                            logger.warning("Unexpected error persisting interrupted status for run %s during shutdown: %r", record.run_id, result)
                        elif result is False:
                            logger.warning("Could not persist interrupted status for run %s during shutdown", record.run_id)

        if pending:
            logger.warning("Run drain exceeded %.1fs on shutdown; %d run task(s) still active and may race checkpointer teardown", timeout, len(pending))
        logger.info("Drained %d in-flight run(s) on shutdown (%d settled within %.1fs)", len(inflight), len(inflight) - len(pending), timeout)


class ConflictError(Exception):
    """Raised when multitask_strategy=reject and thread has inflight runs."""


class UnsupportedStrategyError(Exception):
    """Raised when a multitask_strategy value is not yet implemented."""
