# Nova — Long-Session Frontend Freeze: Production Forensic Investigation

> **Status: Phase 1 in progress.**
> No production code changes will be made until every claim is VERIFIED (code/logs),
> MEASURED (runtime instrumentation), or explicitly UNKNOWN.

## Phase 1 — Complete Ownership Map

Every mutable state involved in the live chat stream, with source of truth,
owner, writers, readers, synchronization, cache layer, invalidation path,
stale-state risk, concurrent-writer risk, and lifecycle.

This map is the contract for what Phase 2 instrumentation must cover and
what Phase 3 reproduction must exercise.

---

### State 1 — `thread.isLoading`

| Field | Value |
|---|---|
| **Source of truth** | `StreamManager.state.isLoading: boolean` (langgraph SDK `dist/ui/manager.js:23-28`) |
| **Owner** | `StreamManager` instance held in React state (`useState(() => new StreamManager(...))`, `stream.lgp.js:124`) |
| **Writers** | `StreamManager.setState()` (manager.js:99-105) — only one writer |
| **Read sites** | `useSyncExternalStore` (stream.lgp.js:129) → propagates to `useStream().isLoading` getter (line 401) → consumed by `useThreadStream` (hooks.ts:1038) → consumed by `page.tsx:377`, `message-list.tsx:208-215, 232, 252, 291, 308, 354-355, 389, 413, 511, 531, 554, 570` |
| **Synchronization** | `useSyncExternalStore` (React 18 concurrent-safe external store) |
| **Cache layer** | None beyond React component tree |
| **Invalidation path** | Manager notifies listeners (manager.js:106-108) → React re-renders consumers |
| **Stale-state risk** | **HIGH** — `isLoading` flips `true` in `enqueue` line 169-172 BEFORE the `for await` starts. Flips `false` only in `finally` line 286 — only when the `for await` loop **exits**. If `await reader.read()` blocks forever, `isLoading` is stuck `true`. |
| **Concurrent writers** | None. Serialized through manager. |
| **Lifecycle** | Created in `useState` initializer on hook mount (stream.lgp.js:124). Survives until component unmount. Reset only on `stream.clear()` (line 300-310) or `enqueue` finally. |

**Read by gates that block user recovery** (verified in source):
- `useActiveRun` polling enabled gate: `enabled && !isStreamLoading` (hooks.ts:1822)
- Rejoin effect early-return: `if (isStreamLoadingRef.current) return;` (hooks.ts:1049)
- Thinking shimmer: `{thread.isLoading && !hasActiveAssistantText && (<Shimmer />)}` (message-list.tsx:570)
- Composer status: `thread.isLoading || activeRun ? "streaming" : ...` (page.tsx:377)

### State 2 — `activeRun` (`Run | null`)

| Field | Value |
|---|---|
| **Source of truth** | TanStack Query cache for `["thread", "active-run", threadId]` (hooks.ts:1780-1782) |
| **Owner** | TanStack Query |
| **Writers** | `queryFn` of `useActiveRun` (hooks.ts:1814-1821) calls `apiClient.runs.list(threadId)` and `pickActiveRun` filters to `pending || running` |
| **Read sites** | Rejoin effect (hooks.ts:1036, 1046, 1052-1067, 1084-1086), page.tsx (line 244, 377), message-list.tsx (line 237-240) |
| **Synchronization** | TanStack Query internal cache + React subscription |
| **Cache layer** | TanStack Query in-memory cache |
| **Invalidation path** | `queryClient.invalidateQueries({ queryKey: activeRunQueryKey(threadId) })` (hooks.ts:1084-1086, line 1378, 1407, 1448, 1019, 1024) |
| **Stale-state risk** | **CRITICAL** — `refetchInterval` only fires while `pickActiveRun(query.state.data)` is truthy (hooks.ts:1824-1825). The query itself is disabled when `isStreamLoading=true` (line 1822). Net effect: when `isLoading` is stuck `true`, polling is suspended AND the run going terminal doesn't trigger an immediate invalidation. |
| **Concurrent writers** | None visible. Single query. |
| **Lifecycle** | Bound to `threadId`. Recreated when `threadId` changes. |

### State 3 — `assistant draft` (in-flight AI message being typed)

| Field | Value |
|---|---|
| **Source of truth** | `MessageTupleManager.chunks: Record<string, { chunk, metadata }>` (langgraph SDK `dist/ui/messages.js:26-47`) |
| **Owner** | `MessageTupleManager` instance held in `StreamManager.messages` |
| **Writers** | `this.messages.add(serialized, metadata)` (manager.js:246) for each `messages-tuple` SSE event |
| **Read sites** | `this.messages.get(messageId, messages.length)` (manager.js:257) inside `setStreamValues` updater |
| **Synchronization** | In-memory map keyed by message id; chunks concatenated via `AIMessageChunk.concat(...)` (messages.js:44) |
| **Cache layer** | None |
| **Invalidation path** | None (state lives until `stream.clear()` clears `messages` (manager.js:308)) |
| **Stale-state risk** | Chunks accumulate forever until `clear()`. If a rejoin replays events, `add()` is called again with the same ids — chunks concat with prior state (line 44). This is the SDK's deduplication mechanism. |
| **Concurrent writers** | The `for await` loop is single-threaded. |
| **Lifecycle** | Survives within `StreamManager` lifetime. Cleared on unmount or `threadId` change. |

### State 4 — `streamed messages` (cumulative `thread.messages`)

| Field | Value |
|---|---|
| **Source of truth** | `StreamManager.state.values` (manager.js:23-28) — set via `setStreamValues` (manager.js:139-148) |
| **Owner** | `StreamManager` |
| **Writers** | `setStreamValues(data)` for `values` events (manager.js:235) and `setStreamValues(prev => ({...prev, ...interruptData}))` for interrupts (line 231-234) |
| **Read sites** | Consumed by `thread.messages` getter (stream.lgp.js:431-433) which calls `getMessages(values)` then `trackStreamMode("messages-tuple", "values")` |
| **Synchronization** | `useSyncExternalStore` |
| **Cache layer** | None beyond StreamManager |
| **Invalidation path** | Manager notifications |
| **Stale-state risk** | **CRITICAL** — for the AI to render with text, the `messages-tuple` events must carry the AIMessageChunk deltas. If the SDK's reader stalls mid-stream, the AI message will appear truncated or absent. |
| **Concurrent writers** | None |
| **Lifecycle** | Same as StreamManager |

### State 5 — `persisted messages` (rendering source)

| Field | Value |
|---|---|
| **Source of truth** | `useMemo(() => hasVisibleStreamState ? thread.messages : [], ...)` (hooks.ts:1092-1095) |
| **Owner** | React (memoized derivation) |
| **Writers** | Re-derived whenever `hasVisibleStreamState` or `thread.messages` change |
| **Read sites** | `mergedMessages` (hooks.ts:1412-1416), `humanMessageCount` (hooks.ts:1100-1102), `pendingUsageMessages` (hooks.ts:1417-1422), `messagesRef.current` (hooks.ts:1402-1404), `appendMessages` (hooks.ts:818) |
| **Synchronization** | React render |
| **Cache layer** | React memo |
| **Invalidation path** | Re-derives on prop change |
| **Stale-state risk** | Tied to `thread.messages`. If SDK stalls, this freezes. |
| **Concurrent writers** | None |
| **Lifecycle** | Tied to component lifecycle |

### State 6 — `thread history` (paginated persisted messages from backend)

| Field | Value |
|---|---|
| **Source of truth** | `useThreadHistory().messages` local state (hooks.ts:1493) populated by `GET /api/threads/{id}/runs/{rid}/messages` |
| **Owner** | `useThreadHistory` hook |
| **Writers** | `loadMessages` callback (hooks.ts:1495-1601) → `setMessages(prev => dedupeMessagesByIdentity([..._messages, ...prev]))` (line 1562-1564) |
| **Read sites** | `history` (hooks.ts:584-589), `visibleHistory` (hooks.ts:1096-1099) → `mergedMessages` (hooks.ts:1413) |
| **Synchronization** | TanStack Query for the upstream `useThreadRuns` (hooks.ts:1752-1769) feeding the loading decision; useState for the local paginated list |
| **Cache layer** | TanStack Query cache for runs list; React state for messages |
| **Invalidation path** | `useThreadRuns` refetch invalidates; otherwise self-driven pagination via `loadMessages` |
| **Stale-state risk** | **MEDIUM** — relies on backend's persisted messages list. If backend is healthy, history loads fine (production evidence: logs show 81+ GETs to `/messages` endpoint with `before_seq` cursor). |
| **Concurrent writers** | None. `loadingRef` guards re-entry (line 1500-1509). |
| **Lifecycle** | Tied to `useThreadStream` lifetime |

### State 7 — `artifacts`

| Field | Value |
|---|---|
| **Source of truth** | `ArtifactsContext` (artifacts/context.tsx:35) — `useState<string[]>([])` |
| **Owner** | React Context |
| **Writers** | `chat-box.tsx:105` (`setArtifacts(thread.values.artifacts)`); also `select`/`deselect` for the selected artifact (context.tsx:44-61) |
| **Read sites** | `chat-box.tsx:51-58`, `message-list.tsx` (via context) |
| **Synchronization** | React Context |
| **Cache layer** | None beyond context |
| **Invalidation path** | Re-renders on prop change of `thread.values.artifacts` |
| **Stale-state risk** | **MEDIUM** — when `thread.values` is stale (SDK stalled), the artifacts list freezes too. |
| **Concurrent writers** | None |
| **Lifecycle** | Provider lifetime |

### State 8 — `terminal output` (sandbox terminal panel)

| Field | Value |
|---|---|
| **Source of truth** | Backend sandbox log files (`.deer-flow/users/{uid}/threads/{tid}/sandbox.log`). Frontend polls `GET /api/sandbox/terminal?...` |
| **Owner** | Sandbox backend |
| **Writers** | Sandbox runtime writes log lines |
| **Read sites** | AgentComputerPanel (presumably) |
| **Synchronization** | HTTP polling |
| **Cache layer** | TanStack Query |
| **Invalidation path** | Poll interval refetch |
| **Stale-state risk** | **LOW** — terminal polls independently of the chat stream; production evidence shows 81+ GETs to `/api/sandbox/terminal?...` succeeding |
| **Concurrent writers** | Sandbox runtime only |
| **Lifecycle** | Per-thread |

### State 9 — `computer state` (AgentComputer panel)

| Field | Value |
|---|---|
| **Source of truth** | `PanelsContext.agentComputerOpen: boolean` (panels/context.tsx:13) + `useAutoOpenAgentComputer` hook |
| **Owner** | React Context |
| **Writers** | `usePanels().setAgentComputerOpen`; `notifyComputerActivity` from `useAutoOpenAgentComputer` |
| **Read sites** | `chat-box.tsx:60`, page.tsx (line 89) |
| **Synchronization** | React Context |
| **Cache layer** | None |
| **Invalidation path** | Re-render on prop change |
| **Stale-state risk** | **LOW** — UI-only state, not stream-dependent |
| **Concurrent writers** | None |
| **Lifecycle** | Provider lifetime |

### State 10 — `subtasks` (per-tool-call UI)

| Field | Value |
|---|---|
| **Source of truth** | `SubtasksContext.tasks: Record<string, Subtask>` (tasks/context.tsx:95) |
| **Owner** | React Context |
| **Writers** | `useUpdateSubtask` (tasks/context.tsx:118-188) — called from `useThreadStream.onCustomEvent` for `task_running` (hooks.ts:883) and from `message-list.tsx:475, 486` for parsed task results |
| **Read sites** | `useSubtask(id)` consumers |
| **Synchronization** | React Context + FSM (tasks/context.tsx:36-56) |
| **Cache layer** | None |
| **Invalidation path** | FSM-protected updates (nextSubtaskStatus) |
| **Stale-state risk** | **LOW** — FSM explicitly guards against overwriting terminal states with derived guesses |
| **Concurrent writers** | Single writer path (useUpdateSubtask) |
| **Lifecycle** | Provider lifetime |

### State 11 — `query cache` (TanStack Query global cache)

| Field | Value |
|---|---|
| **Source of truth** | TanStack Query client held by QueryClientProvider (presumably in `app/layout.tsx`) |
| **Owner** | TanStack Query |
| **Writers** | All `useQuery` and `useMutation` hooks |
| **Read sites** | All `useQuery` consumers |
| **Synchronization** | TanStack Query internal |
| **Cache layer** | TanStack Query in-memory cache + localStorage persistence (if configured) |
| **Invalidation path** | `queryClient.invalidateQueries({ queryKey })` calls |
| **Stale-state risk** | **MEDIUM** — stale query data may persist after the underlying state changed and no invalidation fired |
| **Concurrent writers** | Each query has its own key |
| **Lifecycle** | Process-lifetime |

### State 12 — `stream manager state` (the StreamManager object)

| Field | Value |
|---|---|
| **Source of truth** | `StreamManager` instance |
| **Owner** | React `useState` (stream.lgp.js:124) |
| **Writers** | `setState` (manager.js:99-105), `bumpVersion` (manager.js:40-46), `setStreamValues` (line 139-148) |
| **Read sites** | `subscribe`/`getSnapshot` (manager.js:109-129) |
| **Synchronization** | `useSyncExternalStore` |
| **Cache layer** | None |
| **Invalidation path** | `notifyListeners` (manager.js:106-108) |
| **Stale-state risk** | **HIGH** — state can include `error` from past failures (line 282) without reset; consumers read via `error = stream.error ?? historyError ?? history.error` (stream.lgp.js:391) |
| **Concurrent writers** | Single-threaded within `enqueue` |
| **Lifecycle** | React component lifetime |

### State 13 — `reconnect state` (our rejoin bookkeeping)

| Field | Value |
|---|---|
| **Source of truth** | `rejoinStateRef.current: { runId, attempts, inFlight, exhausted }` (hooks.ts:665-670) |
| **Owner** | React ref |
| **Writers** | Rejoin effect (hooks.ts:1053-1067) and its `.finally` (line 1074-1086) |
| **Read sites** | Rejoin effect (hooks.ts:1052, 1059-1064) |
| **Synchronization** | Single-threaded JS, no race |
| **Cache layer** | None |
| **Invalidation path** | Reset on `runId !== activeRun.run_id` (line 1053-1058); `attempts = 0` after a healthy session (line 1079-1081) |
| **Stale-state risk** | **MEDIUM** — `attempts` is incremented per attempt; budget may exhaust prematurely if `joinedAt - Date.now() > REJOIN_HEALTHY_SESSION_MS` (30s) never becomes true because the join died within 30s |
| **Concurrent writers** | None |
| **Lifecycle** | Tied to `useThreadStream` lifetime |

### State 14 — `SDK state` (the langgraph SDK's internal state)

| Field | Value |
|---|---|
| **Source of truth** | `useStreamLGP` closure variables: `trackStreamModeRef`, `threadIdStreamingRef`, `reconnectRef` (stream.lgp.js:131-160, 358-384) |
| **Owner** | React refs (not in state to avoid re-renders) |
| **Writers** | `useStream` internals |
| **Read sites** | `useStream` internals |
| **Synchronization** | Single-threaded JS |
| **Cache layer** | `sessionStorage` for `reconnectOnMount` (line 104-110) — disabled in our config |
| **Invalidation path** | `stream.clear()` resets `threadIdStreamingRef` indirectly via `stream.state.isLoading=false` |
| **Stale-state risk** | **MEDIUM** — refs can hold stale values across thread changes |
| **Concurrent writers** | None |
| **Lifecycle** | Tied to component lifetime |

### State 15 — `browser reader` (the SSE fetch ReadableStream reader)

| Field | Value |
|---|---|
| **Source of truth** | Local variable `reader` in `streamWithRetry` (utils/stream.js:31, 42) |
| **Owner** | `streamWithRetry` async generator |
| **Writers** | `reader = stream.getReader()` (line 42); `reader.releaseLock()` in finally (line 60) |
| **Read sites** | `await reader.read()` (line 49) |
| **Synchronization** | Single coroutine |
| **Cache layer** | Browser-internal ReadableStream |
| **Invalidation path** | `await reader.cancel()` on signal abort (line 46-47); `reader.releaseLock()` (line 60) |
| **Stale-state risk** | **CRITICAL** — `await reader.read()` (line 49) has **no timeout**. If the underlying TCP socket goes silently dead (no FIN/RST, no TLS alert), this blocks until the OS-level TCP keepalive fires (default 7200s on Linux). The SDK has no idle watchdog. |
| **Concurrent writers** | None |
| **Lifecycle** | Per `enqueue` invocation |

### State 16 — `EventSource/fetch lifecycle`

| Field | Value |
|---|---|
| **Source of truth** | Browser-internal `fetch()` machinery |
| **Owner** | Browser |
| **Writers** | `asyncCaller.fetch` (langgraph SDK) |
| **Read sites** | Browser event loop |
| **Synchronization** | Browser-managed |
| **Cache layer** | Browser HTTP cache |
| **Invalidation path** | `AbortController.abort()` propagates to fetch's signal |
| **Stale-state risk** | **HIGH** — browser fetch behavior under silent TCP failure is platform-dependent. Firefox "Error in input stream" is an **acknowledged** failure mode in our codebase (page.tsx:243 comment). |
| **Concurrent writers** | None |
| **Lifecycle** | Per fetch call |

---

## Dependency Graph (read-only, no code changes implied)

```
[worker.py astream loop]
    │
    │ await bridge.publish(run_id, sse_event, serialized_chunk)
    ▼
[MemoryStreamBridge._RunStream.events list]
    │
    │ bridge.subscribe(run_id, last_event_id) iterator
    ▼
[sse_consumer async generator]
    │
    │ format_sse(entry.event, entry.data, event_id=entry.id)
    ▼
[FastAPI StreamingResponse]
    │
    │ X-Accel-Buffering: no
    ▼
[nginx upstream] ←── proxy_read_timeout 600s, proxy_buffering off
    │
    │ TCP / TLS
    ▼
[Browser fetch API]
    │
    │ response.body.pipeThrough(BytesLineDecoder).pipeThrough(SSEDecoder)
    ▼
[streamWithRetry async iterator] ←── utils/stream.js:23
    │
    │ yield { event, data, id }
    ▼
[StreamManager.enqueue's for await] ←── manager.js:176
    │
    │ this.setStreamValues(...) / this.messages.add(...)
    ▼
[StreamManager.state] ←── observed via useSyncExternalStore
    │
    ├──► React state: thread.isLoading
    ├──► React state: thread.messages
    ├──► React state: thread.values
    └──► React state: thread.error

Parallel paths (read):
    useActiveRun → apiClient.runs.list(threadId)  [gated by !thread.isLoading]
    useThreadHistory → GET /messages              [independent of stream]
    useThreadTokenUsage → GET /token-usage        [independent of stream]

UI renderers (all consume thread.isLoading and/or thread.messages):
    page.tsx:377       → composer status
    message-list.tsx:570 → thinking shimmer
    chat-box.tsx:267   → AgentComputerPanel isLoading
    input-box.tsx:312  → submit/stop toggle
```

## Boundary list (instrumentation targets for Phase 2)

| # | Boundary | Function | Code location | What we need to log |
|---|----------|----------|---------------|---------------------|
| 1 | worker → bridge | `bridge.publish` | `runtime/runs/worker.py:319, 338` | run_id, event_id, mode, payload bytes, monotonic_ts |
| 2 | bridge → sse_consumer | `bridge.subscribe` yield | `runtime/stream_bridge/memory.py:96-126` | run_id, last_event_id, resolved_offset |
| 3 | sse_consumer → wire | `format_sse` yield | `app/gateway/services.py:455, 459, 462` | run_id, event_id, byte_length |
| 4 | wire → browser | nginx access log | `docker/nginx/nginx.conf` | status, bytes_sent, request_time, upstream_response_time |
| 5 | browser → SDK | `streamWithRetry` | `@langchain/langgraph-sdk/dist/utils/stream.js:23-84` | attempt, lastEventId, reader.read() duration |
| 6 | SDK decoder | `SSEDecoder.transform` | `dist/utils/sse.js:61-105` | frame_count, malformed_count |
| 7 | SDK → manager | `for await` loop | `dist/ui/manager.js:176-275` | event type, id, latency-from-prior |
| 8 | manager → React | `setState` notify | `dist/ui/manager.js:99-108` | state diff |
| 9 | React → render | component render | various | thread.isLoading, thread.messages.length |
| 10 | independent poll | `useActiveRun` | `frontend/src/core/threads/hooks.ts:1814-1828` | refetch trigger, activeRun value |

These 10 boundaries give complete pipeline coverage.

## Phase 2 — Instrumentation (DEFERRED FROM ACTUAL CODE CHANGE)

Instrumentation landed as **observability-only, off by default** under two env flags:

* Backend: ``DEER_FLOW_STREAM_TRACE=1`` activates the recorder. ``DEER_FLOW_STREAM_TRACE_FILE=/path/to/file`` writes line-delimited JSON to that path (browser-side fallback is console.debug).
* Frontend: ``NEXT_PUBLIC_NOVA_STREAM_TRACE=1`` activates the recorder. ``NEXT_PUBLIC_NOVA_STREAM_TRACE_FILE=/path`` currently falls back to console.debug because browsers cannot open arbitrary filesystem paths.

Files added/changed:

* ``backend/packages/harness/deerflow/runtime/stream_bridge/diagnostics.py`` — new module, process-singleton recorder.
* ``backend/packages/harness/deerflow/runtime/stream_bridge/memory.py`` — wired to ``publish``, ``publish_end``, ``subscribe``, ``_resolve_start_offset``. Added close-reason tracking (normal end / cancelled / GeneratorExit / exception class name).
* ``backend/packages/harness/deerflow/runtime/runs/worker.py`` — wired ``record_worker_publish`` with astream-to-publish latency capture.
* ``backend/app/gateway/services.py`` — wired ``format_sse_traced`` for byte-count tracking, ``record_sse_consumer_loop_iter`` per iteration, ``record_sse_consumer_disconnect`` for cancel/disconnect classification.
* ``backend/app/gateway/routers/thread_runs.py`` — new ``GET /api/threads/_diagnostics/stream-trace`` endpoint (debug only, returns ``{records: [...]}` from the ring buffer).
* ``frontend/src/core/threads/stream-trace.ts`` — new module mirroring the backend recorder.
* ``frontend/src/env.js`` — added the two ``NEXT_PUBLIC_NOVA_STREAM_TRACE*`` env vars.
* ``frontend/src/core/threads/hooks.ts`` — wired ``lastEventAtRef`` and ``lastEventIdRef`` to ``onLangChainEvent``, recorded rejoin attempts with reason and outcome, added ``recordManagerState`` on ``isLoading`` transitions.

Constraints honored:

* Every recorder is gated by an env flag and is a single boolean check when disabled.
* No production behavior change: the wire format is byte-identical (heartbeat is still ``": heartbeat\n\n"``, every event still has the same ``event:/data:/id:`` ordering).
* All existing tests pass: ``backend pytest tests/test_stream_bridge.py`` (14/14), ``frontend pnpm test tests/unit/core/threads`` (110/110), ``pnpm typecheck`` clean.

## Phase 3 — Reproduction harness (next)

Pending. The instrumentation above gives us the capture surface; the reproduction requires a long-running session that triggers the freeze. Two paths:

1. **Live reproduction**: spin up the dev stack, run a long agent task (e.g. one of the ``alilabsx-domain``-style docs-build sessions), observe the trace. Will block on real LLM latency.
2. **Synthetic reproduction**: a test that injects a stalled SSE reader via the API client wrapper and verifies the frontend state machine.

Until reproduction succeeds on either path, the Phase 4 classification cannot be made with high confidence.