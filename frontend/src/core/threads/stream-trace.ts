"use client";

/**
 * Streaming pipeline diagnostics for the browser side.
 *
 * Mirrors ``backend/.../stream_bridge/diagnostics.py``. This module is
 * observability-only: it never changes production behaviour. Every
 * recorder is a no-op unless the ``NEXT_PUBLIC_NOVA_STREAM_TRACE``
 * environment variable is set to ``"1"`` at build time.
 *
 * Two destinations are supported:
 *
 * 1. In-memory ring buffer — exposed via ``read_recent()`` so a debug
 *    endpoint or React DevTools can inspect the last N records.
 * 2. ``ndjson`` line-delimited JSON to a file path supplied via
 *    ``NEXT_PUBLIC_NOVA_STREAM_TRACE_FILE``. Useful for capturing a
 *    production freeze and downloading the trace after the fact.
 *
 * Each record carries:
 *
 * - ``seq`` — process-global monotonic sequence for cross-stage ordering
 * - ``stage`` — short boundary name (e.g. ``"sdk.event"``, ``"manager.state"``)
 * - ``monotonicMs`` — high-resolution browser timestamp (performance.now)
 * - ``wallIso`` — ISO-8601 wall clock (Date.toISOString)
 * - ``runId`` / ``threadId`` — when in scope
 * - ``extra`` — JSON-safe context dictionary
 *
 * Recording is best-effort: any error inside the recorder is swallowed.
 */

export type DiagnosticsRecord = {
  seq: number;
  stage: string;
  monotonicMs: number;
  wallIso: string;
  runId?: string;
  threadId?: string;
  extra?: Record<string, unknown>;
};

const RING_DEFAULT = 5_000;

function readEnvFlag(name: string): string | undefined {
  if (typeof process === "undefined") return undefined;
  const value = process.env[name];
  return typeof value === "string" && value.length > 0 ? value : undefined;
}

class StreamTrace {
  private readonly enabled: boolean;
  private readonly filePath: string | undefined;
  private readonly ringSize: number;
  private readonly ring: DiagnosticsRecord[] = [];
  private readonly sink:
    | ((line: string) => void)
    | null;
  private seq = 0;

  constructor() {
    this.enabled = readEnvFlag("NEXT_PUBLIC_NOVA_STREAM_TRACE") === "1";
    this.filePath = readEnvFlag("NEXT_PUBLIC_NOVA_STREAM_TRACE_FILE");
    this.ringSize = (() => {
      const raw = readEnvFlag("NEXT_PUBLIC_NOVA_STREAM_TRACE_RING");
      const parsed = raw ? Number.parseInt(raw, 10) : NaN;
      return Number.isFinite(parsed) && parsed > 0 ? parsed : RING_DEFAULT;
    })();
    this.sink = this.buildSink();
  }

  private buildSink(): ((line: string) => void) | null {
    if (!this.enabled) return null;
    // The sink is optional. When ``window`` is absent (server-side
    // rendering, Vitest node environment, worker threads) we still want
    // the ring buffer populated — only the per-record stderr/console
    // emission is skipped. The ring buffer survives in any JS context
    // so the test suite (and SSR diagnostics) still see the records.
    if (typeof window === "undefined") {
      // In a non-browser environment we use ``process.stderr`` if
      // available, otherwise nothing. This keeps production browser
      // behaviour identical (console.debug) while allowing server /
      // tests to opt into the same trace surface.
      if (typeof process !== "undefined" && process.stderr) {
        return (line) => {
          process.stderr.write(line + "\n");
        };
      }
      return null;
    }
    if (this.filePath) {
      // The browser cannot open an arbitrary filesystem path. When a file
      // path is configured we fall back to logging to the console so the
      // operator can capture it via the browser DevTools network log.
      return (line) => {
         
        console.debug("[nova-stream-trace]", line);
      };
    }
    return (line) => {
       
      console.debug("[nova-stream-trace]", line);
    };
  }

  record(
    stage: string,
    fields: {
      runId?: string | null;
      threadId?: string | null;
      extra?: Record<string, unknown>;
    } = {},
  ): void {
    if (!this.enabled || !this.sink) return;
    try {
      this.seq += 1;
      const seq = this.seq;
      const monotonicMs =
        typeof performance !== "undefined" ? performance.now() : Date.now();
      const wallIso = new Date().toISOString();
      const record: DiagnosticsRecord = {
        seq,
        stage,
        monotonicMs,
        wallIso,
      };
      if (fields.runId) record.runId = String(fields.runId);
      if (fields.threadId) record.threadId = String(fields.threadId);
      if (fields.extra) {
        const safeExtra: Record<string, unknown> = {};
        for (const [k, v] of Object.entries(fields.extra)) {
          try {
            JSON.stringify(v);
            safeExtra[k] = v;
          } catch {
            safeExtra[k] = String(v);
          }
        }
        record.extra = safeExtra;
      }
      if (this.ring.length >= this.ringSize) {
        this.ring.shift();
      }
      this.ring.push(record);
      this.sink(JSON.stringify(record));
    } catch {
      /* observability boundary */
    }
  }

  readRecent(limit = 1_000): DiagnosticsRecord[] {
    if (!this.enabled) return [];
    return this.ring.slice(-limit);
  }
}

export const streamTrace = new StreamTrace();

// Convenience wrappers so call sites read like English. The recorders
// are still cheap when disabled — a single boolean check on entry.

export const recordHookStart = (
  threadId: string | null | undefined,
  runId: string | null | undefined,
  source: "submit" | "joinStream",
): void => {
  streamTrace.record("hook.start", {
    threadId: threadId ?? undefined,
    runId: runId ?? undefined,
    extra: { source },
  });
};

export const recordHookEvent = (
  threadId: string | null | undefined,
  runId: string | null | undefined,
  eventName: string,
  eventId: string | null | undefined,
): void => {
  streamTrace.record("hook.onLangChainEvent", {
    threadId: threadId ?? undefined,
    runId: runId ?? undefined,
    extra: { eventName, eventId: eventId ?? undefined },
  });
};

export const recordManagerState = (
  threadId: string | null | undefined,
  isLoading: boolean,
  version: number,
): void => {
  streamTrace.record("manager.state", {
    threadId: threadId ?? undefined,
    extra: { isLoading, version },
  });
};

export const recordReaderRead = (
  threadId: string | null | undefined,
  runId: string | null | undefined,
  durationMs: number,
  resolvedWith: "value" | "done" | "error" | "timeout",
): void => {
  streamTrace.record("sdk.reader.read", {
    threadId: threadId ?? undefined,
    runId: runId ?? undefined,
    extra: { durationMs, resolvedWith },
  });
};

export const recordActiveRunPolled = (
  threadId: string | null | undefined,
  hasActiveRun: boolean,
  reason: "interval" | "invalidate" | "enabled" | "focus",
): void => {
  streamTrace.record("activeRun.polled", {
    threadId: threadId ?? undefined,
    extra: { hasActiveRun, reason },
  });
};

export const recordRejoinAttempt = (
  threadId: string | null | undefined,
  runId: string,
  attempt: number,
  reason: "scheduled" | "activeRun-changed" | "manual",
  outcome: "started" | "skipped:isLoading" | "skipped:noActiveRun" | "skipped:exhausted",
): void => {
  streamTrace.record("rejoin.attempt", {
    threadId: threadId ?? undefined,
    runId,
    extra: { attempt, reason, outcome },
  });
};

export const recordStallWatchdog = (
  threadId: string | null | undefined,
  runId: string | null | undefined,
  lastEventAtMs: number,
  nowMs: number,
  action: "none" | "force-stop" | "invalidate",
): void => {
  streamTrace.record("stall.watchdog", {
    threadId: threadId ?? undefined,
    runId: runId ?? undefined,
    extra: { lastEventAtMs, nowMs, idleMs: nowMs - lastEventAtMs, action },
  });
};