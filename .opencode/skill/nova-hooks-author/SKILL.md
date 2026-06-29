---
name: nova-hooks-author
description: Author or wire a new hook emission in Nova's LangChain agent loop. Use when adding observability for a new tool, when promoting a logged audit event to a structured receipt, or when extending the agent's self-evaluation storage. Nova's hook surface today is `RunJournal` (`backend/packages/harness/deerflow/runtime/journal.py:38`) which implements `BaseCallbackHandler` (on_tool_start / on_tool_end / on_chain_start / on_chain_end); there is no separate `PreToolUse`/`PostToolUse` enum, so any new hook emission must go through `RunJournal.record_middleware` or a new `BaseCallbackHandler` subclass. Triggers on phrases like "add observability for X", "log when tool Y runs", "instrument Z", or before shipping any new middleware.
---

# nova-hooks-author

## The hook surface today (and what it isn't)

| Surface | Status | Where |
|---|---|---|
| `BaseCallbackHandler.on_tool_start` / `on_tool_end` | **Wired** (RunJournal at `worker.py:234-235`) | `backend/packages/harness/deerflow/runtime/journal.py:38` |
| `BaseCallbackHandler.on_chain_start` / `on_chain_end` | **Wired** | same |
| `BaseCallbackHandler.on_llm_end` | **Wired** (per-model token capture) | `subagents/token_collector.py:16` |
| `record_middleware(tag, *, name, hook, action, changes)` | **Dead stub** (only 2 callers) | `runtime/journal.py:529-548` |
| `PreToolUse` / `PostToolUse` enum / event bus | **Does not exist** | — |
| Per-tool-type model routing hook | **Does not exist** | — |

## The recipe: add a new hook emission

1. **Identify the event class.** Is it a `tool.start` / `tool.end` / `chain.start` / `chain.end` (use `BaseCallbackHandler`)? Or a domain event that needs its own handler (define a new `BaseCallbackHandler` subclass and wire it in `worker.py:235`)?

2. **If a LangChain hook:** extend `RunJournal` in `backend/packages/harness/deerflow/runtime/journal.py`. Add a method like `_record_tool_event(tool_name, tool_call_id, payload)` that writes a `RunEvent` to the store.

3. **If a domain event:** add a new `BaseCallbackHandler` subclass (see `subagents/token_collector.py:16` for the shape). Register it in `worker.py:235` alongside `journal`.

4. **Wire SSE emission** so the frontend can consume the event in real time. Add the event type to `core/threads/hooks.ts:onCustomEvent` (currently recognises `task_running`, `llm_retry`, `task_progress`, `verify_result`, `llm_error`).

5. **Update the frontend consumer.** Add the new event type to the switch in `core/threads/hooks.ts:859-960`; route the payload into a new tab in the Agent's Computer panel; if a `MetricCounter` is appropriate, add a pill in `runtime-capabilities-bar.tsx`.

## Test contract

- **Unit:** `backend/tests/test_journal.py` should grow a test that calls the new hook emission with a sample payload and asserts the right `RunEvent` is written.
- **Replay-golden:** the SSE event shape contract (`backend/tests/_replay_fixture.py:113`) must include the new event in `write_read_file.ultra.events.json` if the agent graph exercises the new event path during the recorded scenario.
- **Frontend:** `frontend/tests/unit/core/threads/hooks-*.test.ts` should add a parser test for the new event.

## Anti-patterns (read before authoring)

- ❌ **Don't** add a new `record_middleware` call — the function exists but is dead. If you must log, log via `RunJournal._record_*` methods.
- ❌ **Don't** patch `langgraph` middleware event signatures — that's an upstream contract; instead, expose a new `BaseCallbackHandler` that observes the existing events.
- ❌ **Don't** add an `asyncio.Queue` listener per event type — use the existing `RunEventStore` so the event flow is replayable.
- ❌ **Don't** write to stdout / stderr in a hook — use `logger.debug` or push to `RunEventStore`. stdout breaks the test capture path.
