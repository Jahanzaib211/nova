# Nova — Frontend freeze on long-running agent sessions

## Symptom (live production)

Backend/sandbox keep producing artifacts; sandbox terminal streams; files
change on disk. But the native chat UI freezes: thinking shimmer stays
forever; the input box eventually errors; clicking Stop is the only
escape. Refresh fixes it temporarily, but it returns on long sessions.

This is **not** an execution bug — the run completes server-side and
lands in the DB. It is a stream lifecycle / state synchronization bug
between the frontend SSE consumer and the backend's bounded in-memory
bridge buffer.

## Root cause (5-layer chain)

### Layer A — Bridge buffer is too small

File: `backend/packages/harness/deerflow/runtime/stream_bridge/memory.py:32`

`queue_maxsize: int = 256` is the default.

Long agent sessions emit *thousands* of events (LangChain
`messages-tuple` deltas per token, plus `updates`, `events`, `custom`,
`values`). Each `publish()` call appends and trims:

```python
async def publish(self, run_id, event, data):
    if len(stream.events) > self._maxsize:
        overflow = len(stream.events) - self._maxsize
        del stream.events[:overflow]   # drops oldest events
        stream.start_offset += overflow
```

Once 256 events scroll past, the oldest are gone **forever**. Any
late-joining client (including a rejoin) starts from `start_offset`
and never sees the run's opening frames.

### Layer B — 60s post-end cleanup erases terminal events

File: `backend/packages/harness/deerflow/runtime/runs/worker.py:459`

```python
await bridge.publish_end(run_id)
asyncio.create_task(bridge.cleanup(run_id, delay=60))
```

`MemoryStreamBridge.cleanup()` removes the entire `_RunStream` dict
entry after 60s, regardless of whether any subscriber is still
attached. A rejoin that arrives 90s after the run ended gets
`has_run=False` → `_terminal_end_response` returns the immediate-end
SSE frame → SDK marks the run done without replaying terminal events.

### Layer C — LangGraph SDK's `Last-Event-ID: "-1"` is meaningless

File: `frontend/node_modules/@langchain/langgraph-sdk/dist/react/stream.lgp.js:325-326`

```js
const joinStream = async (runId, lastEventId, joinOptions) => {
    lastEventId ??= "-1";
    ...
    const stream = client.runs.joinStream(threadId, runId, {
        signal,
        lastEventId,
        ...
```

`_resolve_start_offset` walks the buffer looking for the supplied
`last_event_id`. `"-1"` is never found → falls back to
`stream.start_offset` (the *current* buffer head). With a 256-event
cap, this means the rejoin misses everything before the last 256
events — including the human input, the opening "AI is thinking"
marker, and any tool-call metadata needed to rebuild state.

### Layer D — Rejoin retry budget is too aggressive

File: `frontend/src/core/threads/hooks.ts:1777-1778`

```ts
const MAX_REJOIN_ATTEMPTS = 3;
const REJOIN_HEALTHY_SESSION_MS = 30_000;
```

The budget resets only after a join streams for **30 s straight**.
Long agent sessions idle for stretches (during `pip install`,
Docker builds, subagent dispatch, image fetches — all common in
multi-step builds). Each idle stretch kills the SSE → rejoin fires →
hits the 256-event cap → disconnects within 30s → budget exhausted
after 3 attempts → UI permanently stuck.

### Layer E — SDK state vs React state desync after rejoin

When the original SSE drops, `manager.js:286` sets `isLoading=false`.
When the rejoin runs, `onRunCreated` does **not** fire (we're joining
an existing run, not creating one). The SDK begins streaming
mid-buffer, but React state already reflects "done." The
`thread.isLoading` flag re-flips to `true`, but the events needed
to reconstruct the AIMessage from `messages-tuple` deltas are
missing. UI shows "thinking shimmer" forever.

The backend continues executing because the run was created with
`on_disconnect: "continue"` (stream.lgp.js:283) — matching the
"backend keeps working" observation.

## Fix plan

Five layers, each pinned by a regression test, all additive/reversible.
TDD discipline: red → green → refactor → next layer.

---

### Layer A — Bounded but tunable bridge buffer

**Goal**: keep a memory ceiling, raise the default to a level that
survives real sessions, expose it in `config.yaml`.

1. `backend/packages/harness/deerflow/config/stream_bridge_config.py`
   - Raise default `queue_maxsize: int = Field(default=8192, ...)`.
   - Add a docstring with the memory math:
     `concurrent_runs × max_events × avg_event_size`.
   - Add bounds `Field(ge=16, le=131072)`.
2. `backend/packages/harness/deerflow/config/reload_boundary.py`
   - `stream_bridge` already in `STARTUP_ONLY_FIELDS` — confirm; no change.
3. `config.yaml` (root)
   - Add a documented `stream_bridge:` block with the new default,
     matching the schema; mark with `startup-only:` prefix.
4. Tests (`backend/tests/`):
   - `test_stream_bridge_config.py` (new): round-trip the field, default
     value, validation bounds.
   - Extend `tests/test_stream_bridge.py` with a 8192-event burst test
     asserting no `start_offset` advance within window.
   - Extend `tests/test_app_config_reload.py` to assert the new
     default is honored when `stream_bridge` is omitted.

---

### Layer B — Subscriber-aware cleanup with grace window

**Goal**: cleanup only when no subscriber is attached AND a grace
window has elapsed; never during an active rejoin.

1. `backend/packages/harness/deerflow/runtime/stream_bridge/memory.py`
   - Add `_subscriber_count: int` to `_RunStream`.
   - `subscribe()` increments on entry, decrements on `GeneratorExit`
     / final `finally` (use a context-manager helper inside the
     `async for`).
   - `cleanup(run_id, delay=...)`: if `delay > 0`, `await asyncio.sleep(delay)`;
     then **if subscriber count > 0, re-arm** with a shorter retry
     (`delay = 5s`); only pop when count is zero. Loop bounded by a
     hard ceiling (default 600s = 10 min) so abandoned runs still
     release memory.
   - Make the ceiling configurable via a new
     `cleanup_max_wait_seconds: int = Field(default=600)` on
     `StreamBridgeConfig`.
2. `backend/packages/harness/deerflow/runtime/stream_bridge/base.py`
   - Add a `subscriber_count(run_id) -> int` method to the protocol
     so the worker's `cleanup` call can log diagnostics.
3. `backend/packages/harness/deerflow/runtime/runs/worker.py`
   - Log subscriber-driven re-arming at `logger.info`.
4. Tests:
   - `tests/test_stream_bridge.py`:
     - `test_cleanup_defers_while_subscriber_attached`: attach a
       subscriber, end the run, attempt cleanup with delay=0 → must
       NOT pop. Detach, then cleanup must succeed.
     - `test_cleanup_hard_ceiling`: subscriber stays attached past
       ceiling → cleanup forces pop and logs a warning.
     - `test_subscribe_generator_exit_decrements_count`: ensure
       early consumer exit (CancelledError) decrements.

---

### Layer C — Frontend passes a real `Last-Event-ID`

**Goal**: rejoins hand the bridge an ID the buffer actually contains.

1. `frontend/src/core/threads/hooks.ts`
   - Track the **last consumed event id** in a ref, updated in
     `onLangChainEvent` by reading `event.id` from the SDK event.
   - On rejoin, pass `lastEventId = lastConsumedEventIdRef.current ?? "-1"`
     instead of relying on the SDK default.
2. Tests (`frontend/tests/unit/`):
   - Mock `useStream` to emit a sequence of events with ids; assert
     the captured `lastConsumedEventIdRef` updates on each.
   - Assert the rejoin effect invokes `joinStream` with the captured
     id when present.
   - Assert fallback to `"-1"` only when no events have arrived yet
     (cold rejoin — sane default).

---

### Layer D — Rejoin budget tuned for idle-tolerant long sessions

**Goal**: a session that idles should not exhaust its rejoin budget.

1. `frontend/src/core/threads/hooks.ts`
   - Change `MAX_REJOIN_ATTEMPTS = 3` → `MAX_REJOIN_ATTEMPTS = 10`.
   - Change `REJOIN_HEALTHY_SESSION_MS = 30_000` →
     `REJOIN_HEALTHY_SESSION_MS = 5_000`. Rationale: a join that
     survived 5s *did* successfully read from the bridge — that's
     enough signal the run is joinable. The old 30s threshold
     conflated "stream stable" with "join worked," which is wrong
     for sessions with quiet patches.
   - Add a third condition: **the run is still reported as
     pending/running by `useActiveRun`**. If the active-run poll
     says the run is terminal, **stop rejoining** — let the SDK
     settle.
2. Tests:
   - `useThreadStream` unit test that simulates: rejoin fails
     → rejoin succeeds for 6s → rejoin disconnects → rejoin fires
     again (budget restored). Assert budget math.
   - Test that active-run returning `null` mid-budget ends the
     rejoin loop.

---

### Layer E — UI fallback state for "stream lost, run alive"

**Goal**: when stream can't be re-attached, the UI shows a calm
"stream lost" banner with a manual reconnect button.

1. `frontend/src/components/workspace/messages/message-list.tsx`
   - When `!thread.isLoading && activeRun`:
     - Render a slim non-blocking banner above the chat:
       "Stream connection lost. Reconnecting automatically…"
     - Provide a manual **Reconnect** button that calls
       `queryClient.invalidateQueries({ queryKey: activeRunQueryKey(threadId) })`
       (forces the rejoin effect to retry immediately).
2. Composer: while `!thread.isLoading && activeRun`, **disable
   the input**. Show a one-line helper: "Agent is still working —
   wait for it to finish, or Reconnect to take over."
3. Hooks export: add `isStreamLost = !thread.isLoading && activeRun`
   derived value on the return of `useThreadStream`.
4. Tests:
   - Render a chat page in mocked state (`thread.isLoading = false`,
     `activeRun = mockRunningRun`); assert banner + disabled input.
   - Click "Reconnect" → mock `queryClient.invalidateQueries` is
     called with the active-run query key.

---

## Cross-cutting

- **Regression sweep** after each layer:
  ```bash
  cd backend && PYTHONPATH=../scripts \
    uv run pytest tests/test_stream_bridge.py \
                tests/test_stream_bridge_config.py \
                tests/test_app_config_reload.py \
                tests/test_reload_boundary.py \
                tests/test_no_cross_references.py -v
  cd frontend && pnpm test
  ```
- **Documentation**: update `backend/CLAUDE.md` with the new
  `stream_bridge` defaults and the cleanup-while-subscribed semantics.
  Add a `docs/STREAMING.md` section on "Why long sessions need a
  large bridge buffer."
- **Changelog**: `NOVA_CHANGELOG.md` → new sub-heading under v7.5:
  "Frontend stream lifecycle hardening (5-layer fix)."

## Rollback

Each layer is a separate commit. `git revert <sha>` of any one layer
leaves the system in a known good state (the previous layer's fix
still applies). The pre-fix state is the live prod today, so we
land layers one at a time, ship each, observe, then move on.