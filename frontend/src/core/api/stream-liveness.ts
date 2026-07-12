"use client";

/**
 * Transport-level liveness for SSE streams.
 *
 * The gateway emits `: heartbeat` SSE comment frames every 15s
 * (backend services.py sse_consumer). The LangGraph SDK's event parser
 * silently drops comment frames, so no hook-level callback can observe
 * them — which makes "no SDK events for N seconds" indistinguishable
 * from "TCP connection is dead". This module measures liveness where
 * heartbeats are still visible: the raw response byte stream.
 *
 * `livenessFetch` is passed to the SDK via `callerOptions.fetch`
 * (api-client.ts). For `text/event-stream` responses it wraps the body
 * so every received chunk — including heartbeat comments — timestamps
 * the owning thread. Non-stream responses pass through untouched.
 *
 * Consumers (the stall watchdog in core/threads/hooks.ts) read
 * `getStreamLiveness(threadId)`. With 15s server heartbeats, an idle
 * gap of 45s (3 missed heartbeats) on an open stream means the
 * transport is genuinely dead, not just a quiet agent.
 */

export type StreamLivenessSnapshot = {
  /** performance.now() of the most recent byte, or null before any byte. */
  lastByteAt: number | null;
  /** Number of currently-open SSE response bodies for this key. */
  activeStreams: number;
};

function now(): number {
  return typeof performance !== "undefined" ? performance.now() : Date.now();
}

/** Per-thread liveness, keyed by thread id extracted from the request URL. */
const perThread = new Map<
  string,
  { lastByteAt: number; activeStreams: number }
>();
/** Global fallback for URLs whose thread id cannot be parsed. */
const globalState = { lastByteAt: null as number | null, activeStreams: 0 };

/** Bound so a long session can't grow the map without limit. */
const MAX_TRACKED_THREADS = 50;

const THREAD_PATH = /\/threads\/([0-9a-fA-F-]{8,})\//;

/**
 * Phase C0 — per-thread correlation_id registry.
 *
 * The backend emits the run's correlation_id as the first SSE frame:
 *   ``: correlation_id=<hex>\n\n``
 * Standard EventSource implementations ignore the comment line, but our
 * ``livenessFetch`` wrapper tees the first chunk through the decoder so
 * the value can be captured here. Subsequent calls to ``getCorrelationId``
 * return this same identifier for the rest of the session — every stream-trace
 * record, watchdog decision, recovery action, and recovery log carries it.
 */
const correlationIds = new Map<string, string>();
/**
 * SSE comment matcher: ``: correlation_id=<hex>\n``.
 * The hex is variable-length (UUIDv4 hex is 32 chars; with dashes, 36 chars).
 * We accept any length >= 1 to stay forward-compatible and avoid picking up
 * the literal string ``: correlation_id=`` in user payloads.
 */
const CORR_ID_COMMENT = /^: correlation_id=([a-zA-Z0-9_-]+)\s*$/m;

export function extractThreadIdFromUrl(url: string): string | null {
  const match = THREAD_PATH.exec(url);
  return match?.[1] ?? null;
}

/**
 * Read the correlation_id for the given thread, if one has been seen on the
 * SSE stream. Returns ``null`` when the thread has not been seen, the backend
 * did not emit a correlation_id (legacy runs), or the chunk hasn't been read
 * yet. Consumers MUST fall back to ``run_id`` when this returns ``null``.
 */
export function getCorrelationId(threadId?: string | null): string | null {
  if (!threadId) return null;
  return correlationIds.get(threadId) ?? null;
}

/** Test-only: clear all tracked correlation_ids. */
export function resetCorrelationIds(): void {
  correlationIds.clear();
}

/**
 * Inspect a chunk of bytes for the correlation_id comment and record it
 * against the thread. Safe to call on every chunk — the regex is cheap and
 * only the FIRST chunk typically contains the comment, so subsequent calls
 * are no-ops.
 *
 * The decoder is decoded with ``stream: false`` so each chunk's text is
 * fully visible regardless of where byte boundaries land in multi-byte
 * UTF-8 sequences. The correlation_id is a hex string (ASCII-only), so
 * any rare split-codepoint boundary in subsequent chunks does not affect
 * capture.
 *
 * Exported under the ``_FOR_TESTING`` alias so unit tests can drive the
 * capture path directly without going through the full fetch/stream pipeline.
 */
export function captureCorrelationIdFromChunk_FOR_TESTING(
  threadId: string | null,
  chunk: Uint8Array,
  decoder: TextDecoder,
): void {
  if (!threadId) return;
  if (correlationIds.has(threadId)) return;
  // Decode the chunk as a standalone UTF-8 string (stream: false). For
  // SSE comments (ASCII hex), this is correct and lets the regex see
  // the full chunk content.
  const text = decoder.decode(chunk);
  const corr = CORR_ID_COMMENT.exec(text)?.[1];
  if (corr) {
    correlationIds.set(threadId, corr);
  }
}

// Internal alias for production use inside wrapBody.
function captureCorrelationIdFromChunk(
  threadId: string | null,
  chunk: Uint8Array,
  decoder: TextDecoder,
): void {
  captureCorrelationIdFromChunk_FOR_TESTING(threadId, chunk, decoder);
}

function touch(threadId: string | null): void {
  const at = now();
  globalState.lastByteAt = at;
  if (!threadId) return;
  const entry = perThread.get(threadId);
  if (entry) {
    entry.lastByteAt = at;
  }
}

function openStream(threadId: string | null): void {
  globalState.activeStreams += 1;
  globalState.lastByteAt = now();
  if (!threadId) return;
  const entry = perThread.get(threadId);
  if (entry) {
    entry.activeStreams += 1;
    entry.lastByteAt = now();
    return;
  }
  if (perThread.size >= MAX_TRACKED_THREADS) {
    // Evict an arbitrary idle entry (no active streams) to stay bounded.
    for (const [key, value] of perThread) {
      if (value.activeStreams <= 0) {
        perThread.delete(key);
        break;
      }
    }
  }
  perThread.set(threadId, { activeStreams: 1, lastByteAt: now() });
}

function closeStream(threadId: string | null): void {
  globalState.activeStreams = Math.max(0, globalState.activeStreams - 1);
  if (!threadId) return;
  const entry = perThread.get(threadId);
  if (entry) {
    entry.activeStreams = Math.max(0, entry.activeStreams - 1);
  }
}

/**
 * Snapshot liveness for a thread. Falls back to the global signal when
 * the thread has never been tracked (e.g. run created before this module
 * loaded, or URL shape changed).
 */
export function getStreamLiveness(
  threadId?: string | null,
): StreamLivenessSnapshot {
  if (threadId) {
    const entry = perThread.get(threadId);
    if (entry) {
      return {
        lastByteAt: entry.lastByteAt,
        activeStreams: entry.activeStreams,
      };
    }
  }
  return { ...globalState };
}

/** Test-only: reset all tracked state. */
export function resetStreamLiveness(): void {
  perThread.clear();
  globalState.lastByteAt = null;
  globalState.activeStreams = 0;
}

function isEventStreamResponse(response: Response): boolean {
  const contentType = response.headers.get("content-type") ?? "";
  return contentType.includes("text/event-stream");
}

function wrapBody(
  body: ReadableStream<Uint8Array>,
  threadId: string | null,
): ReadableStream<Uint8Array> {
  const reader = body.getReader();
  let closed = false;
  // Phase C0 — a streaming TextDecoder so we can sniff the first chunk for
  // the correlation_id comment without buffering the whole stream. { stream: true }
  // means subsequent chunks concatenate to the same decoder.
  const decoder =
    typeof TextDecoder !== "undefined" ? new TextDecoder("utf-8") : null;
  const close = () => {
    if (closed) return;
    closed = true;
    closeStream(threadId);
  };
  return new ReadableStream<Uint8Array>({
    async pull(controller) {
      let result: ReadableStreamReadResult<Uint8Array>;
      try {
        result = await reader.read();
      } catch (error) {
        close();
        controller.error(error);
        return;
      }
      if (result.done) {
        close();
        controller.close();
        return;
      }
      touch(threadId);
      if (decoder) {
        captureCorrelationIdFromChunk(threadId, result.value, decoder);
      }
      controller.enqueue(result.value);
    },
    cancel(reason) {
      close();
      return reader.cancel(reason);
    },
  });
}

/**
 * Drop-in `fetch` for the SDK client. Identical semantics to
 * `globalThis.fetch` except that `text/event-stream` response bodies are
 * observed for liveness. Must never alter status, headers, or bytes.
 */
export async function livenessFetch(
  input: RequestInfo | URL,
  init?: RequestInit,
): Promise<Response> {
  const response = await globalThis.fetch(input, init);
  if (!response.body || !isEventStreamResponse(response)) {
    return response;
  }
  const url =
    typeof input === "string"
      ? input
      : input instanceof URL
        ? input.toString()
        : input.url;
  const threadId = extractThreadIdFromUrl(url);
  openStream(threadId);
  return new Response(wrapBody(response.body, threadId), {
    status: response.status,
    statusText: response.statusText,
    headers: response.headers,
  });
}
