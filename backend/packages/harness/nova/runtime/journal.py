"""Run event capture via LangChain callbacks.

RunJournal sits between LangChain's callback mechanism and the pluggable
RunEventStore. It standardizes callback data into RunEvent records and
handles token usage accumulation.

Key design decisions:
- on_llm_new_token is NOT implemented -- only complete messages via on_llm_end
- on_chat_model_start captures structured prompts as llm_request (OpenAI format) and
  extracts the first human message for run.input, because it is more reliable than
  on_chain_start (fires on every node) — messages here are fully structured.
- on_chain_start with parent_run_id=None emits a run.start trace marking root invocation.
- on_llm_end emits llm_response in OpenAI Chat Completions format
- Token usage accumulated in memory, written to RunRow on run completion
- Caller identification via tags injection (lead_agent / subagent:{name} / middleware:{name})
"""

from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import Awaitable, Callable, Mapping
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any, cast
from uuid import UUID

from langchain_core.callbacks import BaseCallbackHandler
from langchain_core.messages import AIMessage, AnyMessage, BaseMessage, HumanMessage, ToolMessage
from langgraph.types import Command

if TYPE_CHECKING:
    from nova.runtime.events.store.base import RunEventStore

logger = logging.getLogger(__name__)

# Upper bound on one store write. Every write the journal makes is gated on the
# previous one finishing, so an unbounded write that never returns switches the
# journal off for the rest of the run, silently: run 3cc50519 (2026-11-01)
# counted 626 messages in memory and persisted zero events, and its progress
# snapshot froze an hour before the run stopped. A bounded write fails loudly
# and lets the next flush go out. A batch whose write *timed out* is not sent
# again (see ``_flush_async``); a batch whose write raised is.
DEFAULT_WRITE_TIMEOUT = 30.0

# Backoff between write attempts after a failure: 0.5 s, doubling, capped at
# 30 s, reset by the next successful write. Without it a store that fails fast
# (connection refused, pool exhausted) put the journal into a tight loop — the
# failed batch went back to the buffer, the buffer was still at threshold, and
# the done callback re-flushed at once: 1699 attempts in 0.2 s, a pinned core
# and a warning per attempt.
RETRY_BASE_DELAY_SECONDS = 0.5
RETRY_MAX_DELAY_SECONDS = 30.0

# Only the first failure and every Nth after it are logged while the store
# stays down; the count is in the message.
FAILURE_LOG_EVERY = 10

# Upper bound on events held in memory while the store is down. Past it the
# oldest ordinary events are dropped (counted and logged); run boundaries are
# kept so the stored timeline still says where the run started and ended.
DEFAULT_MAX_BUFFER_EVENTS = 10_000
_KEEP_ON_OVERFLOW = frozenset({"run.start", "run.end", "run.error"})

# The final ``flush()`` gives up after this many write timeouts' worth of
# wall time; the worker publishes the run's end only after it returns.
FLUSH_DEADLINE_WRITE_TIMEOUTS = 3

# Minimum spacing between two run.heartbeat events in the store. This is a
# liveness signal, not a log of activity — one row per minute answers "is the
# journal still writing" and keeps a full-day run under ~1.4k rows.
HEARTBEAT_INTERVAL_SECONDS = 60.0


def _gates(task: asyncio.Task | None, loop: asyncio.AbstractEventLoop) -> bool:
    """Whether an in-flight task should hold back new writes on *loop*.

    Only an unfinished task on the same, still-open loop does. A task parked on
    another loop -- or on one that is closed or no longer runs it -- can never
    finish from here, and waiting on it is exactly how a journal goes quiet.
    """
    if task is None or task.done():
        return False
    task_loop = task.get_loop()
    return task_loop is loop and not task_loop.is_closed()


class RunJournal(BaseCallbackHandler):
    """LangChain callback handler that captures events to RunEventStore."""

    # LangChain dispatches sync handler methods off the event loop, onto a
    # thread-pool thread, unless a handler declares ``run_inline = True``
    # (AsyncCallbackManagerForLLMRun → run_in_executor). On that thread
    # asyncio.get_running_loop() raises, so every mid-run flush and progress
    # snapshot in this class silently no-op'd: run 3cc50519 (2026-11-01)
    # streamed for an hour and persisted zero events until the worker's
    # final flush. These methods only append to buffers and schedule tasks,
    # so running them inline on the loop is cheap. Without this flag mid-run
    # liveness (progress snapshots, heartbeats) does not exist at all.
    run_inline = True

    def __init__(
        self,
        run_id: str,
        thread_id: str,
        event_store: RunEventStore,
        *,
        track_token_usage: bool = True,
        flush_threshold: int = 20,
        progress_reporter: Callable[[dict], Awaitable[None]] | None = None,
        progress_flush_interval: float = 5.0,
        write_timeout: float = DEFAULT_WRITE_TIMEOUT,
        max_buffer_events: int = DEFAULT_MAX_BUFFER_EVENTS,
        flush_deadline: float | None = None,
    ):
        super().__init__()
        self.run_id = run_id
        self.thread_id = thread_id
        self._store = event_store
        self._track_tokens = track_token_usage
        self._flush_threshold = flush_threshold
        self._progress_reporter = progress_reporter
        self._progress_flush_interval = progress_flush_interval
        self._write_timeout = write_timeout
        self._max_buffer_events = max_buffer_events
        self._flush_deadline = flush_deadline if flush_deadline is not None else FLUSH_DEADLINE_WRITE_TIMEOUTS * write_timeout

        # The loop every store write and progress snapshot runs on: the run
        # worker's, where the journal is built. Callbacks also fire on other
        # loops -- subagents run on their own loop in another thread and
        # inherit this handler -- and the store's database connections belong
        # to this loop. A write scheduled on a subagent's loop could neither
        # use nor close its connection, so each one leaked; at one per progress
        # interval that exhausted Postgres' connection slots in six minutes
        # (2026-11-01). Such calls are handed back here instead. ``None`` when
        # built outside a loop (embedded callers, tests): write wherever called.
        try:
            self._owner_loop: asyncio.AbstractEventLoop | None = asyncio.get_running_loop()
        except RuntimeError:
            self._owner_loop = None
        self._handoff_pending: set[str] = set()

        # Write buffer
        self._buffer: list[dict] = []
        self._dropped_events = 0
        # Write-failure backoff (see RETRY_BASE_DELAY_SECONDS). While
        # ``time.monotonic() < _retry_not_before`` no write is started; the
        # timer in ``_retry_handle`` makes the next attempt.
        self._consecutive_failures = 0
        self._retry_not_before = 0.0
        self._retry_handle: asyncio.TimerHandle | None = None
        self._pending_flush_tasks: set[asyncio.Task[None]] = set()
        self._pending_progress_task: asyncio.Task[None] | None = None
        self._pending_progress_delayed = False
        self._progress_dirty = False
        self._last_progress_flush = 0.0

        # Liveness. ``_last_activity_mono`` is the monotonic clock reading of
        # the last event the journal recorded (see ``_put``) — the run-side
        # truth for "has this run done anything recently". The heartbeat
        # event carries that truth into the event store, throttled so a
        # busy run does not flood ``run_events``. Monotonic, not wall
        # clock: comparisons must survive an NTP step. The throttle clock
        # starts at construction, so the first heartbeat fires about a
        # minute into the run — the first event is run.start, which is
        # already a liveness signal on its own.
        self._last_activity_mono = time.monotonic()
        self._started_mono = self._last_activity_mono
        self._last_heartbeat_mono = self._last_activity_mono

        # Token accumulators
        self._total_input_tokens = 0
        self._total_output_tokens = 0
        self._total_tokens = 0
        self._llm_call_count = 0

        # Caller-bucketed token accumulators
        self._lead_agent_tokens = 0
        self._subagent_tokens = 0
        self._middleware_tokens = 0

        # Per-model token accumulator
        self._tokens_by_model: dict[str, dict[str, int]] = {}

        # Dedup: LangChain may fire on_llm_end multiple times for the same run_id
        self._counted_llm_run_ids: set[str] = set()
        self._counted_external_source_ids: set[str] = set()
        self._counted_message_llm_run_ids: set[str] = set()

        # Convenience fields
        self._last_ai_msg: str | None = None
        self._first_human_msg: str | None = None
        self._msg_count = 0
        self._had_llm_error_fallback = False
        self._llm_error_fallback_message: str | None = None

        # Latency tracking
        self._llm_start_times: dict[str, float] = {}  # langchain run_id -> start time

        # LLM request/response tracking
        self._llm_call_index = 0
        self._seen_llm_starts: set[str] = set()  # langchain run_ids that fired on_chat_model_start

    # -- Lifecycle callbacks --

    @staticmethod
    def _message_text(message: BaseMessage) -> str:
        """Extract displayable text from a message's mixed content shape."""
        content = getattr(message, "content", None)
        if isinstance(content, str):
            return content
        if isinstance(content, list):
            parts: list[str] = []
            for block in content:
                if isinstance(block, str):
                    parts.append(block)
                elif isinstance(block, Mapping):
                    text = block.get("text")
                    if isinstance(text, str):
                        parts.append(text)
                    else:
                        nested = block.get("content")
                        if isinstance(nested, str):
                            parts.append(nested)
            return "".join(parts)
        if isinstance(content, Mapping):
            for key in ("text", "content"):
                value = content.get(key)
                if isinstance(value, str):
                    return value

        text = getattr(message, "text", None)
        if isinstance(text, str):
            return text
        return ""

    def _record_message_summary(self, message: BaseMessage, *, caller: str | None = None) -> None:
        """Update run-level convenience fields for persisted run rows."""
        self._msg_count += 1

        # ``last_ai_message`` should represent the lead agent's user-facing
        # answer. Middleware/subagent model calls and empty tool-call-only
        # AI messages must not overwrite the last useful assistant text.
        is_ai_message = isinstance(message, AIMessage) or getattr(message, "type", None) == "ai"
        if is_ai_message and (caller is None or caller == "lead_agent"):
            text = self._message_text(message).strip()
            if text:
                self._last_ai_msg = text[:2000]

    def on_chain_start(
        self,
        serialized: dict[str, Any],
        inputs: dict[str, Any],
        *,
        run_id: UUID,
        parent_run_id: UUID | None = None,
        tags: list[str] | None = None,
        metadata: dict[str, Any] | None = None,
        **kwargs: Any,
    ) -> None:
        caller = self._identify_caller(tags)
        if parent_run_id is None:
            # Root graph invocation — emit a single trace event for the run start.
            chain_name = (serialized or {}).get("name", "unknown")
            self._put(
                event_type="run.start",
                category="trace",
                content={"chain": chain_name},
                metadata={"caller": caller, **(metadata or {})},
            )

    def on_chain_end(
        self,
        outputs: Any,
        *,
        run_id: UUID,
        parent_run_id: UUID | None = None,
        **kwargs: Any,
    ) -> None:
        # Nested chain ends fire for internal graph nodes; only the root chain
        # represents the user-visible run lifecycle.
        if parent_run_id is not None:
            return
        self._put(event_type="run.end", category="outputs", content=outputs, metadata={"status": "success"})
        self._flush_sync()

    def on_chain_error(self, error: BaseException, *, run_id: UUID, **kwargs: Any) -> None:
        self._put(
            event_type="run.error",
            category="error",
            content=str(error),
            metadata={"error_type": type(error).__name__},
        )
        self._flush_sync()

    # -- LLM callbacks --

    def on_chat_model_start(
        self,
        serialized: dict,
        messages: list[list[BaseMessage]],
        *,
        run_id: UUID,
        tags: list[str] | None = None,
        **kwargs: Any,
    ) -> None:
        """Capture structured prompt messages for llm_request event.

        This is also the canonical place to extract the first human message:
        messages are fully structured here, it fires only on real LLM calls,
        and the content is never compressed by checkpoint trimming.
        """
        rid = str(run_id)
        self._llm_start_times[rid] = time.monotonic()
        self._llm_call_index += 1
        self._seen_llm_starts.add(rid)

        logger.debug(
            "on_chat_model_start %s: tags=%s num_batches=%d message_counts=%s",
            run_id,
            tags,
            len(messages),
            [len(batch) for batch in messages],
        )

        # Capture the first human message sent to any LLM in this run.
        if not self._first_human_msg and messages:
            for batch in reversed(messages):
                for m in reversed(batch):
                    if isinstance(m, HumanMessage) and m.name != "summary":
                        caller = self._identify_caller(tags)
                        self.set_first_human_message(m.text)
                        self._put(
                            event_type="llm.human.input",
                            category="message",
                            content=m.model_dump(),
                            metadata={"caller": caller},
                        )
                        self._record_message_summary(m, caller=caller)
                        break
                if self._first_human_msg:
                    break

    def on_llm_start(self, serialized: dict, prompts: list[str], *, run_id: UUID, parent_run_id: UUID | None = None, tags: list[str] | None = None, metadata: dict[str, Any] | None = None, **kwargs: Any) -> None:
        # Fallback: on_chat_model_start is preferred. This just tracks latency.
        self._llm_start_times[str(run_id)] = time.monotonic()

    def on_llm_end(
        self,
        response: Any,
        *,
        run_id: UUID,
        parent_run_id: UUID | None = None,
        tags: list[str] | None = None,
        **kwargs: Any,
    ) -> None:
        messages: list[AnyMessage] = []
        logger.debug("on_llm_end %s: tags=%s", run_id, tags)
        for generation in response.generations:
            for gen in generation:
                if hasattr(gen, "message"):
                    messages.append(gen.message)
                else:
                    logger.warning(f"on_llm_end {run_id}: generation has no message attribute: {gen}")

        for message in messages:
            caller = self._identify_caller(tags)

            # Latency
            rid = str(run_id)
            start = self._llm_start_times.pop(rid, None)
            latency_ms = int((time.monotonic() - start) * 1000) if start else None

            # Token usage from message
            usage = getattr(message, "usage_metadata", None)
            usage_dict = dict(usage) if usage else {}
            additional_kwargs = getattr(message, "additional_kwargs", None) or {}
            if isinstance(additional_kwargs, dict) and additional_kwargs.get("deerflow_error_fallback"):
                self._had_llm_error_fallback = True
                detail = additional_kwargs.get("error_detail")
                reason = additional_kwargs.get("error_reason")
                fallback_text = self._message_text(message).strip()
                if isinstance(detail, str) and detail.strip():
                    self._llm_error_fallback_message = detail.strip()
                elif isinstance(reason, str) and reason.strip():
                    self._llm_error_fallback_message = reason.strip()
                elif fallback_text:
                    self._llm_error_fallback_message = fallback_text[:2000]

            # Resolve call index
            call_index = self._llm_call_index
            if rid not in self._seen_llm_starts:
                # Fallback: on_chat_model_start was not called
                self._llm_call_index += 1
                call_index = self._llm_call_index
                self._seen_llm_starts.add(rid)

            # Trace event: llm_response (OpenAI completion format)
            self._put(
                event_type="llm.ai.response",
                category="message",
                content=message.model_dump(),
                metadata={
                    "caller": caller,
                    "usage": usage_dict,
                    "latency_ms": latency_ms,
                    "llm_call_index": call_index,
                },
            )
            if rid not in self._counted_message_llm_run_ids:
                self._record_message_summary(message, caller=caller)

            # Token accumulation (dedup by langchain run_id to avoid double-counting
            # when the callback fires more than once for the same response)
            if self._track_tokens:
                input_tk = usage_dict.get("input_tokens", 0) or 0
                output_tk = usage_dict.get("output_tokens", 0) or 0
                total_tk = usage_dict.get("total_tokens", 0) or 0
                if total_tk == 0:
                    total_tk = input_tk + output_tk
                if total_tk > 0 and rid not in self._counted_llm_run_ids:
                    self._counted_llm_run_ids.add(rid)
                    self._total_input_tokens += input_tk
                    self._total_output_tokens += output_tk
                    self._total_tokens += total_tk
                    self._llm_call_count += 1

                    if caller.startswith("subagent:"):
                        self._subagent_tokens += total_tk
                    elif caller.startswith("middleware:"):
                        self._middleware_tokens += total_tk
                    else:
                        self._lead_agent_tokens += total_tk

                    # Per-model bucket
                    response_metadata = getattr(message, "response_metadata", None) or {}
                    per_call_model: str | None = None
                    if isinstance(response_metadata, Mapping):
                        per_call_model = response_metadata.get("model_name") or response_metadata.get("model")
                    self._record_model_usage(per_call_model, input_tk, output_tk, total_tk)

                    self._schedule_progress_flush()

        if messages:
            self._counted_message_llm_run_ids.add(str(run_id))

    def on_llm_error(self, error: BaseException, *, run_id: UUID, **kwargs: Any) -> None:
        self._llm_start_times.pop(str(run_id), None)
        self._put(event_type="llm.error", category="trace", content=str(error))

    def on_tool_start(self, serialized, input_str, *, run_id, parent_run_id=None, tags=None, metadata=None, inputs=None, **kwargs):
        """Handle tool start event, cache tool call ID for later correlation"""
        tool_call_id = str(run_id)
        logger.debug("Tool start for node %s, tool_call_id=%s, tags=%s", run_id, tool_call_id, tags)

    def on_tool_end(self, output, *, run_id, parent_run_id=None, **kwargs):
        """Handle tool end event, append message and clear node data"""
        try:
            if isinstance(output, ToolMessage):
                msg = cast(ToolMessage, output)
                self._put(event_type="llm.tool.result", category="message", content=msg.model_dump())
                self._record_message_summary(msg)
            elif isinstance(output, Command):
                cmd = cast(Command, output)
                messages = cmd.update.get("messages", [])
                for message in messages:
                    if isinstance(message, BaseMessage):
                        self._put(event_type="llm.tool.result", category="message", content=message.model_dump())
                        self._record_message_summary(message)
                    else:
                        logger.warning(f"on_tool_end {run_id}: command update message is not BaseMessage: {type(message)}")
            else:
                logger.warning(f"on_tool_end {run_id}: output is not ToolMessage: {type(output)}")
        finally:
            logger.debug("Tool end for node %s", run_id)

    # -- Internal methods --

    def _put(self, *, event_type: str, category: str, content: str | dict = "", metadata: dict | None = None) -> None:
        self._last_activity_mono = time.monotonic()
        heartbeat_metadata: dict | None = None
        since_heartbeat = self._last_activity_mono - self._last_heartbeat_mono
        if event_type != "run.heartbeat" and since_heartbeat >= HEARTBEAT_INTERVAL_SECONDS:
            # Record the heartbeat BEFORE we append it, so the call below
            # cannot re-trigger this branch (no recursion).
            self._last_heartbeat_mono = self._last_activity_mono
            heartbeat_metadata = {
                "elapsed_seconds": round(self._last_activity_mono - self._started_mono, 1),
                "llm_call_count": self._llm_call_count,
                "total_tokens": self._total_tokens,
                "buffered_events": len(self._buffer),
            }
        self._buffer.append(
            {
                "thread_id": self.thread_id,
                "run_id": self.run_id,
                "event_type": event_type,
                "category": category,
                "content": content,
                "metadata": metadata or {},
                "created_at": datetime.now(UTC).isoformat(),
            }
        )
        if heartbeat_metadata is not None:
            self._buffer.append(
                {
                    "thread_id": self.thread_id,
                    "run_id": self.run_id,
                    "event_type": "run.heartbeat",
                    "category": "liveness",
                    "content": "",
                    "metadata": heartbeat_metadata,
                    "created_at": datetime.now(UTC).isoformat(),
                }
            )
        self._enforce_buffer_cap()
        # A heartbeat is only worth anything once it is in the store: waiting
        # for the threshold would hold it in memory exactly when the run is
        # quiet (a long tool call), which is when liveness matters.
        if heartbeat_metadata is not None or event_type == "run.heartbeat" or len(self._buffer) >= self._flush_threshold:
            self._flush_sync()

    def _enforce_buffer_cap(self) -> None:
        overflow = len(self._buffer) - self._max_buffer_events
        if overflow <= 0:
            return
        kept: list[dict] = []
        dropped = 0
        for event in self._buffer:
            if dropped < overflow and event.get("event_type") not in _KEEP_ON_OVERFLOW:
                dropped += 1
                continue
            kept.append(event)
        if not dropped:
            return
        self._buffer = kept
        before = self._dropped_events
        self._dropped_events += dropped
        if before == 0 or before // 1000 != self._dropped_events // 1000:
            logger.warning(
                "Journal for run %s: buffer full (%d events) while the store is unavailable — dropped %d oldest event(s) so far",
                self.run_id,
                self._max_buffer_events,
                self._dropped_events,
            )

    def _flush_sync(self) -> None:
        """Best-effort flush of buffer to RunEventStore.

        BaseCallbackHandler methods are synchronous.  If an event loop is
        running we schedule an async ``put_batch``; otherwise the events
        stay in the buffer and are flushed later by the async ``flush()``
        call in the worker's ``finally`` block.
        """
        if not self._buffer:
            return
        if time.monotonic() < self._retry_not_before:
            # Backing off after a failed write; the scheduled retry sends
            # these. Writing now is what turned a down store into a spin.
            return
        loop = self._write_loop("flush", self._flush_sync)
        if loop is None:
            # Not on the owning loop (handed off) or no loop at all — the
            # events stay buffered for the hand-off or the final flush.
            return
        # One write in flight at a time — avoids concurrent writes to the same
        # SQLite file. Events that arrive meanwhile stay buffered and go out
        # in the follow-up flush _on_flush_done schedules. Tasks that can no
        # longer finish from this loop are dropped from the gate, not obeyed.
        stale = {t for t in self._pending_flush_tasks if not t.done() and not _gates(t, loop)}
        if stale:
            logger.warning("Journal for run %s: dropping %d flush task(s) that cannot complete on the current event loop", self.run_id, len(stale))
        self._pending_flush_tasks = {t for t in self._pending_flush_tasks if _gates(t, loop)}
        if self._pending_flush_tasks:
            return
        # Swap rather than copy-then-clear: a callback on another thread can
        # append between the two, and that event would be cleared unsent.
        batch, self._buffer = self._buffer, []
        task = loop.create_task(self._flush_async(batch))
        self._pending_flush_tasks.add(task)
        task.add_done_callback(self._on_flush_done)

    async def _flush_async(self, batch: list[dict]) -> None:
        try:
            async with asyncio.timeout(self._write_timeout):
                await self._store.put_batch(batch)
        except TimeoutError:
            # Not re-sent, deliberately. The timeout cannot tell "never
            # written" from "committed, acknowledgement late", and the store
            # assigns seq numbers on insert with no dedupe key, so a re-send
            # of a committed batch stores every event twice. A possibly-lost
            # batch, logged with its size, is the lesser harm.
            self._record_failure()
            logger.warning(
                "Journal write for run %s timed out after %.0f s — %d event(s) not re-sent (they may already be stored)",
                self.run_id,
                self._write_timeout,
                len(batch),
            )
        except Exception:
            # The store refused the write, so nothing was stored: return
            # the batch to the buffer for the backed-off retry.
            self._buffer = batch + self._buffer
            self._enforce_buffer_cap()
            failures = self._record_failure()
            if failures == 1 or failures % FAILURE_LOG_EVERY == 0:
                logger.warning(
                    "Failed to flush %d events for run %s (%d consecutive failure(s)) — retrying in %.2f s",
                    len(batch),
                    self.run_id,
                    failures,
                    self._next_retry_delay(),
                    exc_info=True,
                )
        else:
            self._record_success()

    def _next_retry_delay(self) -> float:
        if self._consecutive_failures <= 0:
            return 0.0
        return min(RETRY_BASE_DELAY_SECONDS * 2 ** (self._consecutive_failures - 1), RETRY_MAX_DELAY_SECONDS)

    def _record_failure(self) -> int:
        self._consecutive_failures += 1
        self._retry_not_before = time.monotonic() + self._next_retry_delay()
        return self._consecutive_failures

    def _record_success(self) -> None:
        if self._consecutive_failures:
            logger.info("Journal for run %s recovered after %d failed write(s)", self.run_id, self._consecutive_failures)
        self._consecutive_failures = 0
        self._retry_not_before = 0.0
        self._cancel_retry()

    def _cancel_retry(self) -> None:
        if self._retry_handle is not None:
            self._retry_handle.cancel()
            self._retry_handle = None

    def _schedule_retry(self, loop: asyncio.AbstractEventLoop) -> None:
        if self._retry_handle is not None or loop.is_closed():
            return
        delay = max(0.0, self._retry_not_before - time.monotonic())
        self._retry_handle = loop.call_later(delay, self._retry_flush)

    def _retry_flush(self) -> None:
        self._retry_handle = None
        self._retry_not_before = 0.0
        self._flush_sync()

    def _on_flush_done(self, task: asyncio.Task) -> None:
        self._pending_flush_tasks.discard(task)
        if task.cancelled():
            return
        exc = task.exception()
        if exc:
            logger.warning("Journal flush task failed: %s", exc)
        if self._consecutive_failures:
            # The write failed: retry after the backoff, never from here
            # directly — the buffer is still at threshold, so an immediate
            # re-flush is the tight loop this replaced.
            self._schedule_retry(task.get_loop())
            return
        # Events that reached the threshold while this write was in flight
        # were held back by the one-at-a-time gate; send them now instead of
        # waiting for the next event to arrive (or for the run to end).
        if len(self._buffer) >= self._flush_threshold:
            self._flush_sync()

    def _identify_caller(self, tags: list[str] | None) -> str:
        _tags = tags or []
        for tag in _tags:
            if isinstance(tag, str) and (tag.startswith("subagent:") or tag.startswith("middleware:") or tag == "lead_agent"):
                return tag
        # Default to lead_agent: the main agent graph does not inject
        # callback tags, while subagents and middleware explicitly tag
        # themselves.
        return "lead_agent"

    def _record_model_usage(
        self,
        model_name: str | None,
        input_tokens: int,
        output_tokens: int,
        total_tokens: int,
    ) -> None:
        """Add a single LLM call's token usage to the per-model accumulator.

        Missing / empty ``model_name`` collapses into a shared ``"unknown"``
        bucket so the breakdown stays usable when a provider doesn't surface
        ``response_metadata.model_name``.
        """
        if total_tokens <= 0:
            return
        bucket = self._tokens_by_model.setdefault(
            model_name or "unknown",
            {"input_tokens": 0, "output_tokens": 0, "total_tokens": 0},
        )
        bucket["input_tokens"] += int(input_tokens or 0)
        bucket["output_tokens"] += int(output_tokens or 0)
        bucket["total_tokens"] += int(total_tokens)

    # -- Public methods (called by worker) --

    @property
    def last_activity_age_seconds(self) -> float:
        """Seconds since the journal last recorded an event (monotonic clock).

        A live run touches the journal constantly (chain/tool/LLM callbacks),
        so a large value means the run has produced nothing recently —
        workers and watchdogs can read this without querying the store.
        """
        return time.monotonic() - self._last_activity_mono

    def record_external_llm_usage_records(
        self,
        records: list[dict[str, int | str | None]],
    ) -> None:
        """Record token usage from external sources (e.g., subagents).

        Each record should contain:
            source_run_id: Unique identifier to prevent double-counting
            caller: Caller tag (e.g. "subagent:general-purpose")
            model_name: Real per-call model name (str or None; falls back to
                ``"unknown"`` bucket when missing)
            input_tokens: Input token count
            output_tokens: Output token count
            total_tokens: Total token count (computed from input+output if 0/missing)
        """
        if not self._track_tokens:
            return
        for record in records:
            source_id = str(record.get("source_run_id", ""))
            if not source_id:
                continue
            if source_id in self._counted_external_source_ids:
                continue

            total_tk = record.get("total_tokens", 0) or 0
            if total_tk <= 0:
                input_tk = record.get("input_tokens", 0) or 0
                output_tk = record.get("output_tokens", 0) or 0
                total_tk = input_tk + output_tk
            if total_tk <= 0:
                continue

            input_tk = record.get("input_tokens", 0) or 0
            output_tk = record.get("output_tokens", 0) or 0

            self._counted_external_source_ids.add(source_id)
            self._total_input_tokens += input_tk
            self._total_output_tokens += output_tk
            self._total_tokens += total_tk

            caller = str(record.get("caller", ""))
            if caller.startswith("subagent:"):
                self._subagent_tokens += total_tk
            elif caller.startswith("middleware:"):
                self._middleware_tokens += total_tk
            else:
                self._lead_agent_tokens += total_tk

            self._record_model_usage(record.get("model_name"), input_tk, output_tk, total_tk)

            self._schedule_progress_flush()

    def set_first_human_message(self, content: str) -> None:
        """Record the first human message for convenience fields."""
        self._first_human_msg = content[:2000] if content else None

    def record_middleware(self, tag: str, *, name: str, hook: str, action: str, changes: dict) -> None:
        """Record a middleware state-change event.

        Called by middleware implementations when they perform a meaningful
        state change (e.g., title generation, summarization, HITL approval).
        Pure-observation middleware should not call this.

        Args:
            tag: Short identifier for the middleware (e.g., "title", "summarize",
                 "guardrail"). Used to form event_type="middleware:{tag}".
            name: Full middleware class name.
            hook: Lifecycle hook that triggered the action (e.g., "after_model").
            action: Specific action performed (e.g., "generate_title").
            changes: Dict describing the state changes made.
        """
        self._put(
            event_type=f"middleware:{tag}",
            category="middleware",
            content={"name": name, "hook": hook, "action": action, "changes": changes},
        )

    async def flush(self) -> None:
        """Force flush remaining buffer. Called in worker's finally block.

        Bounded throughout: the worker publishes the run's ``end`` only after
        this returns, so an unbounded wait here would keep a finished run open.
        In-flight writes are bounded by their own timeout; one stuck on another
        loop is not waited for at all. The whole call is bounded by
        ``flush_deadline``: a store that keeps failing is retried with backoff
        until then, after which the undelivered count is logged as an error
        and the last write error is raised (the worker logs and continues).
        """
        loop = asyncio.get_running_loop()
        deadline = time.monotonic() + self._flush_deadline
        # From here on this method makes every write attempt itself.
        self._cancel_retry()
        # A finishing write may schedule a follow-up (see _on_flush_done), so
        # drain until none is in flight rather than waiting once.
        while inflight := [t for t in self._pending_flush_tasks if _gates(t, loop)]:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                logger.warning("Journal for run %s: final flush stopped waiting for %d in-flight write(s)", self.run_id, len(inflight))
                break
            await asyncio.wait(inflight, timeout=remaining)
            self._cancel_retry()
        while _gates(self._pending_progress_task, loop):
            if self._pending_progress_delayed:
                self._pending_progress_task.cancel()
                await asyncio.gather(self._pending_progress_task, return_exceptions=True)
                self._progress_dirty = False
                self._pending_progress_delayed = False
                break
            await asyncio.gather(self._pending_progress_task, return_exceptions=True)

        last_exc: BaseException | None = None
        possibly_written = 0
        while self._buffer:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                break
            batch = self._buffer[: self._flush_threshold]
            del self._buffer[: self._flush_threshold]
            try:
                async with asyncio.timeout(min(self._write_timeout, remaining)):
                    await self._store.put_batch(batch)
            except TimeoutError as exc:
                # Not re-sent, for the reason given in _flush_async.
                last_exc = exc
                possibly_written += len(batch)
                self._record_failure()
            except Exception as exc:
                last_exc = exc
                self._buffer = batch + self._buffer
                failures = self._record_failure()
                if failures == 1 or failures % FAILURE_LOG_EVERY == 0:
                    logger.warning("Final flush for run %s failed (%d consecutive failure(s))", self.run_id, failures, exc_info=True)
                delay = min(self._next_retry_delay(), deadline - time.monotonic())
                if delay > 0:
                    await asyncio.sleep(delay)
            else:
                self._record_success()

        if self._buffer or possibly_written:
            logger.error(
                "Journal for run %s: final flush gave up after %.1f s — %d event(s) undelivered (%d of them in timed-out writes that may have been stored)",
                self.run_id,
                self._flush_deadline,
                len(self._buffer) + possibly_written,
                possibly_written,
            )
            if last_exc is None:
                last_exc = TimeoutError(f"journal flush deadline of {self._flush_deadline:.1f} s exceeded")
            raise last_exc

    def _schedule_progress_flush(self) -> None:
        """Best-effort throttled progress snapshot for active run visibility."""
        if self._progress_reporter is None:
            return
        now = time.monotonic()
        elapsed = now - self._last_progress_flush
        if elapsed < self._progress_flush_interval:
            self._progress_dirty = True
            self._schedule_delayed_progress_flush(self._progress_flush_interval - elapsed)
            return
        loop = self._write_loop("progress", self._schedule_progress_flush)
        if loop is None:
            return
        if _gates(self._pending_progress_task, loop):
            self._progress_dirty = True
            return
        self._progress_dirty = False
        self._pending_progress_task = loop.create_task(self._flush_progress_async(snapshot=self.get_completion_data()))

    def _write_loop(self, kind: str, retry: Callable[[], None]) -> asyncio.AbstractEventLoop | None:
        """The loop to write on now, or ``None`` after handing the call off.

        On the owning loop (or anywhere, for a journal built outside a loop)
        that loop is returned. Called from any other loop or thread, ``retry``
        is scheduled onto the owning loop -- at most one pending hand-off per
        ``kind``, since a burst of subagent events needs one write, not one per
        event -- and ``None`` tells the caller not to write here.
        """
        try:
            current: asyncio.AbstractEventLoop | None = asyncio.get_running_loop()
        except RuntimeError:
            current = None
        owner = self._owner_loop
        if owner is None or current is owner:
            return current
        if owner.is_closed() or kind in self._handoff_pending:
            return None
        self._handoff_pending.add(kind)

        def _run() -> None:
            self._handoff_pending.discard(kind)
            retry()

        try:
            owner.call_soon_threadsafe(_run)
        except RuntimeError:
            # The owning loop closed between the check and the call.
            self._handoff_pending.discard(kind)
        return None

    def _schedule_delayed_progress_flush(self, delay: float) -> None:
        loop = self._write_loop("progress-delayed", lambda: self._schedule_delayed_progress_flush(delay))
        if loop is None:
            return
        if _gates(self._pending_progress_task, loop):
            return
        delay = max(0.0, delay)
        self._pending_progress_delayed = delay > 0
        self._pending_progress_task = loop.create_task(self._flush_progress_async(delay=delay))

    async def _flush_progress_async(self, *, snapshot: dict | None = None, delay: float = 0.0) -> None:
        if self._progress_reporter is None:
            return
        if delay > 0:
            self._pending_progress_delayed = True
            await asyncio.sleep(delay)
            self._pending_progress_delayed = False
        dirty_before_write = self._progress_dirty
        self._progress_dirty = False
        snapshot_to_write = snapshot or self.get_completion_data()
        try:
            async with asyncio.timeout(self._write_timeout):
                await self._progress_reporter(snapshot_to_write)
            self._last_progress_flush = time.monotonic()
        except TimeoutError:
            logger.warning("Progress snapshot for run %s timed out after %.0f s", self.run_id, self._write_timeout)
            self._progress_dirty = True
        except Exception:
            logger.warning("Failed to persist progress snapshot for run %s", self.run_id, exc_info=True)
        if dirty_before_write or self._progress_dirty:
            self._progress_dirty = False
            self._pending_progress_task = None
            self._schedule_delayed_progress_flush(self._progress_flush_interval)

    def get_completion_data(self) -> dict:
        """Return accumulated token and message data for run completion."""
        return {
            "total_input_tokens": self._total_input_tokens,
            "total_output_tokens": self._total_output_tokens,
            "total_tokens": self._total_tokens,
            "llm_call_count": self._llm_call_count,
            "lead_agent_tokens": self._lead_agent_tokens,
            "subagent_tokens": self._subagent_tokens,
            "middleware_tokens": self._middleware_tokens,
            "token_usage_by_model": {model: dict(usage) for model, usage in self._tokens_by_model.items()},
            "message_count": self._msg_count,
            "last_ai_message": self._last_ai_msg,
            "first_human_message": self._first_human_msg,
        }

    @property
    def had_llm_error_fallback(self) -> bool:
        return self._had_llm_error_fallback

    @property
    def llm_error_fallback_message(self) -> str | None:
        return self._llm_error_fallback_message
