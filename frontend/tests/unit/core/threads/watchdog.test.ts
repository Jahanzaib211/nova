import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import {
  classifyVerifyOutcome,
  evaluateWatchdog,
  shouldArmWatchdog,
} from "@/core/threads/stream-trace";

const RECORDER_MODULE = "@/core/threads/stream-trace";

beforeEach(() => {
  vi.resetModules();
});

afterEach(() => {
  delete process.env.NEXT_PUBLIC_NOVA_STREAM_TRACE;
  delete process.env.NEXT_PUBLIC_NOVA_STREAM_TRACE_FILE;
});

describe("evaluateWatchdog — production stall decision", () => {
  const THRESHOLD_MS = 60_000;

  it("does not fire when isLoading is false (stream closed cleanly)", () => {
    const decision = evaluateWatchdog({
      isLoading: false,
      lastEventAtMs: 0,
      transportLastByteAtMs: 0,
      nowMs: THRESHOLD_MS + 10_000,
      thresholdMs: THRESHOLD_MS,
      lastFireAtMs: null,
    });
    expect(decision.fire).toBe(false);
    expect(decision.reason).toBe("not-loading");
  });

  it("does not fire when in cooldown after a recent fire", () => {
    const now = 1_000_000;
    const decision = evaluateWatchdog({
      isLoading: true,
      lastEventAtMs: null,
      transportLastByteAtMs: now - THRESHOLD_MS - 1,
      nowMs: now,
      thresholdMs: THRESHOLD_MS,
      cooldownMs: THRESHOLD_MS,
      lastFireAtMs: now - 5_000, // 5s after fire → still in cooldown
    });
    expect(decision.fire).toBe(false);
    expect(decision.reason).toBe("in-cooldown");
  });

  it("DOES fire when cooldown has elapsed (re-fire allowed, not once-per-run)", () => {
    const now = 1_000_000;
    const decision = evaluateWatchdog({
      isLoading: true,
      lastEventAtMs: null,
      transportLastByteAtMs: now - THRESHOLD_MS - 1,
      nowMs: now,
      thresholdMs: THRESHOLD_MS,
      cooldownMs: THRESHOLD_MS,
      lastFireAtMs: now - THRESHOLD_MS - 1, // cooldown elapsed
    });
    expect(decision.fire).toBe(true);
    expect(decision.signal).toBe("transport");
  });

  it("does not fire when no baseline exists AND no transport signal exists", () => {
    const decision = evaluateWatchdog({
      isLoading: true,
      lastEventAtMs: null,
      transportLastByteAtMs: null,
      nowMs: 30_000,
      thresholdMs: THRESHOLD_MS,
      lastFireAtMs: null,
    });
    expect(decision.fire).toBe(false);
    expect(decision.reason).toBe("no-baseline");
  });

  it("does not fire on transport signal when idle is below threshold", () => {
    const now = 1_000_000;
    const decision = evaluateWatchdog({
      isLoading: true,
      lastEventAtMs: null,
      transportLastByteAtMs: now - 30_000,
      nowMs: now,
      thresholdMs: THRESHOLD_MS,
      lastFireAtMs: null,
    });
    expect(decision.fire).toBe(false);
    expect(decision.reason).toBe("below-threshold");
  });

  it("fires on transport signal when idle exceeds threshold (dead TCP)", () => {
    const now = 1_000_000;
    const decision = evaluateWatchdog({
      isLoading: true,
      lastEventAtMs: now - 200_000,
      transportLastByteAtMs: now - THRESHOLD_MS - 1,
      nowMs: now,
      thresholdMs: THRESHOLD_MS,
      lastFireAtMs: null,
    });
    expect(decision.fire).toBe(true);
    expect(decision.reason).toBe("armed-and-stalled");
    expect(decision.signal).toBe("transport");
  });

  it("prefers transport signal over SDK signal when both are present", () => {
    const now = 1_000_000;
    const decision = evaluateWatchdog({
      isLoading: true,
      lastEventAtMs: now - 200_000,
      transportLastByteAtMs: now - 1_000,
      nowMs: now,
      thresholdMs: THRESHOLD_MS,
      lastFireAtMs: null,
    });
    expect(decision.fire).toBe(false);
    expect(decision.reason).toBe("below-threshold");
  });

  it("falls back to SDK-event signal when transport signal is absent", () => {
    const now = 1_000_000;
    const decision = evaluateWatchdog({
      isLoading: true,
      lastEventAtMs: now - THRESHOLD_MS - 1,
      transportLastByteAtMs: null,
      nowMs: now,
      thresholdMs: THRESHOLD_MS,
      sdkFallbackThresholdMs: THRESHOLD_MS,
      lastFireAtMs: null,
    });
    expect(decision.fire).toBe(true);
    expect(decision.signal).toBe("sdk-events");
  });

  it("uses sdkFallbackThresholdMs (more generous) when transport is absent", () => {
    const now = 1_000_000;
    const decision = evaluateWatchdog({
      isLoading: true,
      lastEventAtMs: now - 30_000,
      transportLastByteAtMs: null,
      nowMs: now,
      thresholdMs: THRESHOLD_MS,
      sdkFallbackThresholdMs: 120_000,
      lastFireAtMs: null,
    });
    expect(decision.fire).toBe(false);
  });
});

describe("shouldArmWatchdog — Phase 2 rewire: arm on isLoading alone", () => {
  it("arms when isLoading=true and threadId set, no activeRun required", () => {
    const d = shouldArmWatchdog({
      isMock: false,
      threadId: "thread-a",
      isLoading: true,
      intervalAlreadyArmed: false,
    });
    expect(d.arm).toBe(true);
  });

  it("does not arm in mock mode", () => {
    const d = shouldArmWatchdog({
      isMock: true,
      threadId: "thread-a",
      isLoading: true,
      intervalAlreadyArmed: false,
    });
    expect(d.arm).toBe(false);
    expect(d.reason).toBe("mock");
  });

  it("does not arm when threadId is missing", () => {
    const d = shouldArmWatchdog({
      isMock: false,
      threadId: null,
      isLoading: true,
      intervalAlreadyArmed: false,
    });
    expect(d.arm).toBe(false);
    expect(d.reason).toBe("no-thread");
  });

  it("does not arm when not loading", () => {
    const d = shouldArmWatchdog({
      isMock: false,
      threadId: "thread-a",
      isLoading: false,
      intervalAlreadyArmed: false,
    });
    expect(d.arm).toBe(false);
    expect(d.reason).toBe("not-loading");
  });

  it("does not arm a second time without an intervening disarm", () => {
    const d = shouldArmWatchdog({
      isMock: false,
      threadId: "thread-a",
      isLoading: true,
      intervalAlreadyArmed: true,
    });
    expect(d.arm).toBe(false);
    expect(d.reason).toBe("already-armed");
  });
});

describe("classifyVerifyOutcome — verify-then-act decision", () => {
  it("classifies a terminal run as run-terminal", () => {
    const out = classifyVerifyOutcome({
      runs: [
        { run_id: "r1", status: "success" },
        { run_id: "r2", status: "running" },
      ],
      expectedRunId: "r1",
    });
    expect(out.kind).toBe("run-terminal");
    expect(out.runId).toBe("r1");
  });

  it("classifies a still-running run as run-running", () => {
    const out = classifyVerifyOutcome({
      runs: [{ run_id: "r1", status: "running" }],
      expectedRunId: "r1",
    });
    expect(out.kind).toBe("run-running");
    expect(out.runId).toBe("r1");
  });

  it("classifies a missing expected run as run-missing", () => {
    const out = classifyVerifyOutcome({
      runs: [{ run_id: "r2", status: "running" }],
      expectedRunId: "r1",
    });
    expect(out.kind).toBe("run-missing");
    expect(out.runId).toBeNull();
  });

  it("classifies null runs (verify call failed) as verify-failed", () => {
    const out = classifyVerifyOutcome({
      runs: null,
      expectedRunId: "r1",
    });
    expect(out.kind).toBe("verify-failed");
  });

  it("without expectedRunId: any active run → run-running", () => {
    const out = classifyVerifyOutcome({
      runs: [{ run_id: "r1", status: "pending" }],
      expectedRunId: null,
    });
    expect(out.kind).toBe("run-running");
    expect(out.runId).toBe("r1");
  });

  it("without expectedRunId: no active runs → run-missing", () => {
    const out = classifyVerifyOutcome({
      runs: [{ run_id: "r1", status: "success" }],
      expectedRunId: null,
    });
    expect(out.kind).toBe("run-missing");
  });

  it("treats interrupted status as terminal", () => {
    const out = classifyVerifyOutcome({
      runs: [{ run_id: "r1", status: "interrupted" }],
      expectedRunId: "r1",
    });
    expect(out.kind).toBe("run-terminal");
  });

  it("treats timeout status as terminal", () => {
    const out = classifyVerifyOutcome({
      runs: [{ run_id: "r1", status: "timeout" }],
      expectedRunId: "r1",
    });
    expect(out.kind).toBe("run-terminal");
  });
});

describe("recordStallWatchdog — gated + structured", () => {
  it("is a no-op when disabled", async () => {
    const { recordStallWatchdog, streamTrace } = await import(RECORDER_MODULE);
    recordStallWatchdog("thread-a", "run-b", 1_000, 100_000, "force-stop");
    expect(streamTrace.readRecent()).toEqual([]);
  });

  it("emits a structured record with idleMs computed correctly when enabled", async () => {
    process.env.NEXT_PUBLIC_NOVA_STREAM_TRACE = "1";
    const { recordStallWatchdog, streamTrace } = await import(RECORDER_MODULE);
    recordStallWatchdog("thread-a", "run-b", 1_000, 100_000, "force-stop");
    const recent = streamTrace.readRecent();
    expect(recent.length).toBe(1);
    const r = recent[0]!;
    expect(r.stage).toBe("stall.watchdog");
    expect(r.threadId).toBe("thread-a");
    expect(r.runId).toBe("run-b");
    expect(r.extra).toEqual({
      lastEventAtMs: 1_000,
      nowMs: 100_000,
      idleMs: 99_000,
      action: "force-stop",
    });
  });
});
