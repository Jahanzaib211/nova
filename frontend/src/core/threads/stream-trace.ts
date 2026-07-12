"use client";

import { getCorrelationId } from "../api/stream-liveness";

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
  /**
   * Phase C0 — cross-process correlation identifier emitted by the
   * backend on every SSE stream. Set automatically from the threadId
   * registry when ``record()`` is called; consumers MUST treat it as
   * the canonical cross-process join key. When absent, fall back to
   * ``runId`` for legacy / pre-Phase-C0 runs.
   */
  correlationId?: string;
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
  private readonly sink: ((line: string) => void) | null;
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
      // Phase C0 — surface the cross-process correlation_id. The
      // stream-liveness module tees the backend's ``: correlation_id=``
      // SSE comment into a per-thread registry; ``getCorrelationId``
      // reads from it. We resolve once per record so the captured
      // identifier is exactly what the backend saw at run start.
      const threadKey = fields.threadId ?? null;
      const corr = getCorrelationId(threadKey);
      if (corr) record.correlationId = corr;
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
  outcome:
    | "started"
    | "skipped:isLoading"
    | "skipped:noActiveRun"
    | "skipped:exhausted",
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
  action: "none" | "force-stop" | "invalidate" | "converge-idle",
): void => {
  streamTrace.record("stall.watchdog", {
    threadId: threadId ?? undefined,
    runId: runId ?? undefined,
    extra: { lastEventAtMs, nowMs, idleMs: nowMs - lastEventAtMs, action },
  });
};

// ---------------------------------------------------------------------------
// Additional stage recorders (render, completion, recovery, cleanup).
//
// All are gated by ``NEXT_PUBLIC_NOVA_STREAM_TRACE=1`` (same flag as the
// existing recorders) and are no-ops when disabled. Each call is a single
// boolean check + structured-log emission so the disabled-overhead is
// effectively zero.
// ---------------------------------------------------------------------------

export const recordStateMerge = (
  threadId: string | null | undefined,
  runId: string | null | undefined,
  messageCount: number,
  lastMessageId: string | null,
): void => {
  streamTrace.record("state.merge", {
    threadId: threadId ?? undefined,
    runId: runId ?? undefined,
    extra: { messageCount, lastMessageId: lastMessageId ?? undefined },
  });
};

export const recordRender = (
  threadId: string | null | undefined,
  runId: string | null | undefined,
  component: string,
  lastMessageId: string | null,
): void => {
  streamTrace.record("render", {
    threadId: threadId ?? undefined,
    runId: runId ?? undefined,
    extra: { component, lastMessageId: lastMessageId ?? undefined },
  });
};

export const recordThinkingIndicator = (
  threadId: string | null | undefined,
  runId: string | null | undefined,
  visible: boolean,
  streaming: boolean,
): void => {
  streamTrace.record("thinking.indicator", {
    threadId: threadId ?? undefined,
    runId: runId ?? undefined,
    extra: { visible, streaming },
  });
};

export const recordComposer = (
  threadId: string | null | undefined,
  runId: string | null | undefined,
  status: "streaming" | "ready" | "error",
  hasActiveRun: boolean,
): void => {
  streamTrace.record("composer", {
    threadId: threadId ?? undefined,
    runId: runId ?? undefined,
    extra: { status, hasActiveRun },
  });
};

export const recordRecovery = (
  threadId: string | null | undefined,
  runId: string,
  reason:
    | "activeRun-poll"
    | "watchdog-force-stop"
    | "watchdog-converge-idle"
    | "watchdog-force-stop-unverified"
    | "stop-timeout-forced"
    | "force-disconnect"
    | "manual-rejoin"
    | "page-reload",
  attempts: number,
): void => {
  streamTrace.record("recovery", {
    threadId: threadId ?? undefined,
    runId,
    extra: { reason, attempts },
  });
};

export const recordReconnect = (
  threadId: string | null | undefined,
  runId: string,
  outcome:
    | "started"
    | "skipped:isLoading"
    | "skipped:noActiveRun"
    | "skipped:exhausted"
    | "joined"
    | "failed",
): void => {
  streamTrace.record("reconnect", {
    threadId: threadId ?? undefined,
    runId,
    extra: { outcome },
  });
};

export const recordCompletion = (
  threadId: string | null | undefined,
  runId: string | null | undefined,
  finalStatus: "success" | "error" | "interrupted" | "timeout",
  messageCount: number,
): void => {
  streamTrace.record("completion", {
    threadId: threadId ?? undefined,
    runId: runId ?? undefined,
    extra: { finalStatus, messageCount },
  });
};

export const recordCleanup = (
  threadId: string | null | undefined,
  reason:
    | "unmount"
    | "thread-switch"
    | "reset"
    | "logout"
    | "force-disconnect"
    | "stop",
  armedWatchdogs: number,
): void => {
  streamTrace.record("cleanup", {
    threadId: threadId ?? undefined,
    extra: { reason, armedWatchdogs },
  });
};

// ---------------------------------------------------------------------------
// Watchdog pure-function — exported so it can be unit-tested without React.
//
// The hook integrates these signals; this helper centralises the rule
// ``should I fire the recovery path?`` so behaviour is testable in isolation.
// ---------------------------------------------------------------------------

export type WatchdogDecision = {
  fire: boolean;
  /** Which liveness signal produced the verdict (present when fire=true). */
  signal?: "transport" | "sdk-events";
  reason:
    | "armed-and-stalled"
    | "not-loading"
    | "in-cooldown"
    | "below-threshold"
    | "no-baseline";
};

export function evaluateWatchdog(args: {
  isLoading: boolean;
  /** SDK-level: performance.now() of the last parsed stream event. */
  lastEventAtMs: number | null;
  /**
   * Transport-level: performance.now() of the last received byte on the
   * thread's SSE response (includes heartbeat comment frames the SDK
   * parser drops). null when no transport signal is available.
   */
  transportLastByteAtMs?: number | null;
  nowMs: number;
  /**
   * Idle threshold for the transport signal. The gateway heartbeats every
   * 15s, so 45s = 3 missed heartbeats = the connection is genuinely dead.
   */
  thresholdMs: number;
  /**
   * Idle threshold for the SDK-event fallback when no transport signal
   * exists. Must be generous: a quiet agent (long tool call) emits no SDK
   * events while the transport stays healthy.
   */
  sdkFallbackThresholdMs?: number;
  /**
   * performance.now() of the last watchdog fire, or null if the watchdog
   * has not fired yet for this run. Used to enforce a cooldown so a
   * single stalled stream cannot be force-stopped in a tight loop.
   * Pass null to allow immediate fire.
   */
  lastFireAtMs?: number | null;
  /**
   * Minimum delay between fires. Defaults to the transport threshold so a
   * sustained stall produces roughly one recovery attempt per cooldown
   * window — long enough for the recovery to take effect, short enough
   * that a second stall in the same run is recoverable.
   */
  cooldownMs?: number;
}): WatchdogDecision {
  if (!args.isLoading) return { fire: false, reason: "not-loading" };

  const transportAt = args.transportLastByteAtMs ?? null;
  const lastFireAt = args.lastFireAtMs ?? null;
  const cooldownMs = args.cooldownMs ?? args.thresholdMs;
  if (lastFireAt !== null && args.nowMs - lastFireAt < cooldownMs) {
    return { fire: false, reason: "in-cooldown" };
  }

  if (transportAt !== null) {
    const idleMs = args.nowMs - transportAt;
    if (idleMs < args.thresholdMs) {
      return { fire: false, reason: "below-threshold" };
    }
    return { fire: true, signal: "transport", reason: "armed-and-stalled" };
  }

  if (args.lastEventAtMs === null) {
    return { fire: false, reason: "no-baseline" };
  }
  const idleMs = args.nowMs - args.lastEventAtMs;
  const fallbackThreshold = args.sdkFallbackThresholdMs ?? args.thresholdMs;
  if (idleMs < fallbackThreshold) {
    return { fire: false, reason: "below-threshold" };
  }
  return { fire: true, signal: "sdk-events", reason: "armed-and-stalled" };
}

/**
 * Pure decision: should the watchdog interval arm at all?
 *
 * Phase 2 rewire: the watchdog now arms whenever the SDK is loading
 * (``thread.isLoading``) regardless of whether the active-run poll has
 * data yet. The primary-path freeze (stream wedges in the first 60 s
 * before ``useActiveRun`` has data) is what this fixes.
 */
export function shouldArmWatchdog(args: {
  isMock: boolean;
  threadId: string | null | undefined;
  isLoading: boolean;
  intervalAlreadyArmed: boolean;
}): {
  arm: boolean;
  reason: "mock" | "no-thread" | "not-loading" | "already-armed";
} {
  if (args.isMock) return { arm: false, reason: "mock" };
  if (!args.threadId) return { arm: false, reason: "no-thread" };
  if (!args.isLoading) return { arm: false, reason: "not-loading" };
  if (args.intervalAlreadyArmed) return { arm: false, reason: "already-armed" };
  return { arm: true, reason: "already-armed" };
}

/**
 * Pure decision: classify the server-side state returned by the verify
 * call after a watchdog fire. Each outcome maps to a different recovery
 * action — terminal/missing runs need LOCAL teardown only; running runs
 * need force-stop + rejoin.
 */
export type VerifyOutcome = {
  kind: "run-terminal" | "run-missing" | "run-running" | "verify-failed";
  runId: string | null;
};

export function classifyVerifyOutcome(args: {
  runs: ReadonlyArray<{ run_id: string; status: string }> | null;
  expectedRunId: string | null | undefined;
}): VerifyOutcome {
  if (args.runs === null) {
    return { kind: "verify-failed", runId: null };
  }
  // Look for the expected run first.
  if (args.expectedRunId) {
    const expected = args.runs.find((r) => r.run_id === args.expectedRunId);
    if (!expected) {
      return { kind: "run-missing", runId: null };
    }
    if (
      expected.status === "success" ||
      expected.status === "error" ||
      expected.status === "interrupted" ||
      expected.status === "timeout"
    ) {
      return { kind: "run-terminal", runId: expected.run_id };
    }
    return { kind: "run-running", runId: expected.run_id };
  }
  // No expected id: any active run means stream missed its end event;
  // no active run means stream is genuinely done.
  const active = args.runs.find(
    (r) => r.status === "pending" || r.status === "running",
  );
  if (active) {
    return { kind: "run-running", runId: active.run_id };
  }
  return { kind: "run-missing", runId: null };
}

// ---------------------------------------------------------------------------
// Phase 4 — bounded Stop + Force Disconnect helpers.
//
// Every streaming state must have an exit that fires within bounded time.
// These helpers are pure (no React, no SDK) so they can be unit-tested in
// isolation.
// ---------------------------------------------------------------------------

export type StopOutcome =
  | { kind: "stopped" }
  | { kind: "force-disconnected"; reason: "stop-timeout" | "user-force" }
  | { kind: "noop"; reason: "not-loading" | "already-stopped" };

export type StopState = "idle" | "stopping" | "stopped" | "force-disconnected";

/**
 * Pure decision: given the current stop state and the user action, what is
 * the next state? Each transition has a bounded timeout associated with it.
 *
 *   idle → stopping          (user pressed Stop)
 *   stopping → stopped       (server acknowledged within 5s)
 *   stopping → force-disconnected (server did NOT acknowledge within 5s)
 *   stopped → idle           (user pressed Stop again — clear)
 *   force-disconnected → idle (user pressed Stop again — clear)
 *   any → idle               (compose escape — composer ready, dismissedRunId set)
 */
export function nextStopState(
  current: StopState,
  action: "stop" | "force" | "tick" | "escape",
  msSinceActionStart: number,
  serverAckedWithinMs: boolean,
): { next: StopState; outcome: StopOutcome } {
  if (action === "escape") {
    return {
      next: "idle",
      outcome: { kind: "noop", reason: "already-stopped" },
    };
  }
  if (action === "force") {
    return {
      next: "force-disconnected",
      outcome: { kind: "force-disconnected", reason: "user-force" },
    };
  }
  if (action === "stop") {
    return {
      next: "stopping",
      outcome: { kind: "stopped" },
    };
  }
  // action === "tick" — only meaningful when in stopping
  if (current !== "stopping") {
    return { next: current, outcome: { kind: "noop", reason: "not-loading" } };
  }
  if (serverAckedWithinMs) {
    return { next: "stopped", outcome: { kind: "stopped" } };
  }
  if (msSinceActionStart >= 5_000) {
    return {
      next: "force-disconnected",
      outcome: { kind: "force-disconnected", reason: "stop-timeout" },
    };
  }
  return { next: "stopping", outcome: { kind: "stopped" } };
}

/**
 * Pure decision: should the composer show Stop (server-truth running) or
 * be forced ready (after escape / dismiss)?
 *
 * The composer's status is normally derived from ``thread.isLoading ||
 * activeRun``. The escape hatch is a per-run ``dismissedRunId``: when the
 * user has explicitly given up on a run (Stop timeout → Force Disconnect),
 * we suppress the "streaming" status so the composer is usable. The
 * dismissed id clears when the run reaches terminal state or a new run
 * starts.
 */
export function composerShouldStream(args: {
  threadIsLoading: boolean;
  hasActiveRun: boolean;
  activeRunId: string | null;
  dismissedRunId: string | null;
  threadError: unknown;
}): "streaming" | "ready" | "error" {
  if (args.threadError) return "error";
  if (!args.threadIsLoading && !args.hasActiveRun) return "ready";
  if (args.dismissedRunId && args.dismissedRunId === args.activeRunId) {
    return "ready";
  }
  return "streaming";
}

// ---------------------------------------------------------------------------
// Phase 5 — convergent teardown helper.
//
// Every streaming state must have an exit that fires within bounded time,
// and all exits must converge on the same teardown path so the same
// invariants are upheld regardless of trigger. This is the inventory of
// valid teardown reasons — exhaustiveness makes it impossible to add a new
// caller without also considering its trace classification.
// ---------------------------------------------------------------------------

export type TeardownReason =
  | "watchdog-force-stop"
  | "watchdog-converge-idle"
  | "stop-timeout-forced"
  | "force-disconnect"
  | "manual-stop"
  | "unmount"
  | "thread-switch"
  | "logout"
  | "reset";

export const TEARDOWN_REASONS: ReadonlyArray<TeardownReason> = [
  "watchdog-force-stop",
  "watchdog-converge-idle",
  "stop-timeout-forced",
  "force-disconnect",
  "manual-stop",
  "unmount",
  "thread-switch",
  "logout",
  "reset",
] as const;

/**
 * Pure decision: does a teardown step belong to a group that needs to run
 * through the convergent teardown helper? Used by tests to assert every
 * caller funnels through the same entry point.
 */
export function isValidTeardownReason(
  reason: string,
): reason is TeardownReason {
  return (TEARDOWN_REASONS as ReadonlyArray<string>).includes(reason);
}
