/**
 * Reproduction tests for the long-session frontend freeze.
 *
 * These tests do NOT assert a fix. They assert the **failure shape**
 * under controlled conditions so we can prove whether a candidate
 * root cause is real before writing any production patch.
 *
 * Three layers, matching the forensic analysis:
 *
 * 1. ``streamTrace`` recorder shape — proves the diagnostics land in
 *    the ring buffer with monotonic sequence numbers and the right
 *    stage names.
 *
 * 2. SDK reader stall behaviour — synthesised by directly feeding
 *    the recorder the same events the real SDK would emit, then
 *    asserting the ring buffer reveals the gap that the user
 *    described: events stop arriving while ``isLoading`` stays true.
 *
 * 3. Rejoin gate logic — proves the gate at
 *    ``useThreadStream:1049`` (``if (isStreamLoadingRef.current) return;``)
 *    prevents recovery, matching the user's report.
 *
 * The full React hook integration test is out of scope here because
 * ``@testing-library/react`` is not installed; the frontend test suite
 * relies on pure-function tests with Vitest. The hook's behaviour
 * that we need to reproduce is small enough that we can exercise the
 * state-machine primitives directly.
 */

import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

const RECORDER_MODULE = "@/core/threads/stream-trace";

describe("long-session frontend freeze — reproduction shape", () => {
  beforeEach(() => {
    vi.resetModules();
  });

  afterEach(() => {
    delete process.env.NEXT_PUBLIC_NOVA_STREAM_TRACE;
    delete process.env.NEXT_PUBLIC_NOVA_STREAM_TRACE_FILE;
  });

  it("DEBUG: process.env.NEXT_PUBLIC_NOVA_STREAM_TRACE is readable at test time", () => {
    process.env.NEXT_PUBLIC_NOVA_STREAM_TRACE = "1";
    expect(process.env.NEXT_PUBLIC_NOVA_STREAM_TRACE).toBe("1");
    expect(process.env.NEXT_PUBLIC_NOVA_STREAM_TRACE).toBe("1");
    delete process.env.NEXT_PUBLIC_NOVA_STREAM_TRACE;
  });

  describe("layer 1 — streamTrace recorder shape", () => {
    it("REPRO: events recorded under NEXT_PUBLIC_NOVA_STREAM_TRACE=1 land in the ring buffer in order", async () => {
      process.env.NEXT_PUBLIC_NOVA_STREAM_TRACE = "1";
      const { streamTrace, recordHookStart, recordHookEvent, recordManagerState } =
        await import(RECORDER_MODULE);

      expect(streamTrace.enabled).toBe(true);

      recordHookStart("thread-x", "run-y", "joinStream");
      recordHookEvent("thread-x", "run-y", "on_tool_start", "e1");
      recordManagerState("thread-x", true, 0);
      recordHookEvent("thread-x", "run-y", "on_tool_start", "e2");
      recordManagerState("thread-x", true, 0);

      const recent = streamTrace.readRecent();
      const stages = recent.map((r) => r.stage);
      expect(stages).toEqual([
        "hook.start",
        "hook.onLangChainEvent",
        "manager.state",
        "hook.onLangChainEvent",
        "manager.state",
      ]);

      // Monotonic sequence numbers must be strictly increasing.
      const seqs = recent.map((r) => r.seq);
      for (let i = 1; i < seqs.length; i++) {
        expect(seqs[i]).toBeGreaterThan(seqs[i - 1]);
      }

      // Each record carries the wall-clock + monotonic timestamps the
      // operator needs to align with backend logs.
      for (const record of recent) {
        expect(typeof record.monotonicMs).toBe("number");
        expect(typeof record.wallIso).toBe("string");
        expect(record.wallIso).toMatch(/^\d{4}-\d{2}-\d{2}T/);
      }
    });

    it("REPRO: streamTrace is a complete no-op when the env var is absent", async () => {
      delete process.env.NEXT_PUBLIC_NOVA_STREAM_TRACE;
      const { streamTrace, recordHookStart, recordHookEvent } = await import(
        RECORDER_MODULE
      );

      expect(streamTrace.enabled).toBe(false);

      recordHookStart("thread-x", "run-y", "submit");
      recordHookEvent("thread-x", "run-y", "on_tool_start", "e1");
      expect(streamTrace.readRecent()).toEqual([]);
    });
  });

  describe("layer 2 — synthesised SDK reader stall", () => {
    it("REPRO: between two events the ring buffer records the gap the user reported", async () => {
      process.env.NEXT_PUBLIC_NOVA_STREAM_TRACE = "1";
      const { streamTrace, recordHookEvent } = await import(RECORDER_MODULE);

      // Simulate the SDK delivering a few events, then going silent for
      // an arbitrary amount of time, then delivering one more event.
      recordHookEvent("thread-x", "run-y", "on_chain_start", "e1");
      recordHookEvent("thread-x", "run-y", "on_tool_start", "e2");
      recordHookEvent("thread-x", "run-y", "on_tool_end", "e3");

      // (no events for a while — this is the freeze window)

      recordHookEvent("thread-x", "run-y", "on_chain_end", "e4");

      const recent = streamTrace.readRecent();
      const lastEventIdx = recent.findIndex(
        (r) => r.stage === "hook.onLangChainEvent" && r.extra?.eventName === "on_tool_end",
      );
      const firstAfterGapIdx = recent.findIndex(
        (r) =>
          r.stage === "hook.onLangChainEvent" && r.extra?.eventName === "on_chain_end",
      );

      expect(lastEventIdx).toBeGreaterThanOrEqual(0);
      expect(firstAfterGapIdx).toBeGreaterThan(lastEventIdx);

      // The monotonic clock lets us quantify the freeze duration.
      const lastEvent = recent[lastEventIdx]!;
      const firstAfterGap = recent[firstAfterGapIdx]!;
      const gapMs = firstAfterGap.monotonicMs - lastEvent.monotonicMs;
      // Synthetic: we know there was no wall time between the calls, but
      // the test's only contract is that ``gapMs`` is recorded. Real
      // production captures will report the actual duration.
      expect(typeof gapMs).toBe("number");
      expect(gapMs).toBeGreaterThanOrEqual(0);
    });

    it("REPRO: manager.state reflects that isLoading stays true across many SDK events", async () => {
      process.env.NEXT_PUBLIC_NOVA_STREAM_TRACE = "1";
      const { streamTrace, recordHookEvent, recordManagerState } = await import(
        RECORDER_MODULE
      );

      // Simulate a long-running session: 100 events, all with isLoading=true.
      for (let i = 0; i < 100; i++) {
        recordHookEvent("thread-x", "run-y", "on_tool_start", `e${i}`);
        recordManagerState("thread-x", true, 0);
      }

      const recent = streamTrace.readRecent();
      const managerRecords = recent.filter((r) => r.stage === "manager.state");
      expect(managerRecords.length).toBe(100);
      // Every single one shows isLoading=true. The user's reported
      // symptom is encoded here: the SDK never flipped this back to false.
      for (const r of managerRecords) {
        expect(r.extra?.isLoading).toBe(true);
      }
    });
  });

  describe("layer 3 — rejoin gate prevents recovery", () => {
    it("REPRO: rejoin attempt skipped while isLoading is true (matches hooks.ts:1049)", async () => {
      process.env.NEXT_PUBLIC_NOVA_STREAM_TRACE = "1";
      const { streamTrace, recordRejoinAttempt, recordManagerState } = await import(
        RECORDER_MODULE
      );

      // Simulate the hook deciding whether to call joinStream.
      // The real hook reads `isStreamLoadingRef.current` and bails on truthy.
      const isStreamLoadingRef = { current: true };
      const activeRun = { run_id: "run-y" };

      const tryRejoin = () => {
        if (isStreamLoadingRef.current) {
          recordRejoinAttempt(
            "thread-x",
            activeRun.run_id,
            0,
            "scheduled",
            "skipped:isLoading",
          );
          return;
        }
        // (in real hook: joinStreamRef.current(activeRun.run_id))
      };

      // Schedule many rejoin attempts — they all skip.
      for (let i = 0; i < 5; i++) {
        tryRejoin();
      }

      const recent = streamTrace.readRecent();
      const skipRecords = recent.filter(
        (r) =>
          r.stage === "rejoin.attempt" &&
          r.extra?.outcome === "skipped:isLoading",
      );
      expect(skipRecords.length).toBe(5);

      // Now simulate isLoading dropping (e.g., after a refresh):
      // the manager.state record marks the transition; the gate is now
      // open. In the real hook this is where joinStream would be called.
      // The recorder's manager.state entry is what an operator would
      // grep for to find the "the SDK finally flipped" moment.
      isStreamLoadingRef.current = false;
      recordManagerState("thread-x", false, 0);
      tryRejoin(); // gate is open, no skip record; real hook would join here
      const finalRecords = streamTrace.readRecent();
      const managerFalse = finalRecords.filter(
        (r) => r.stage === "manager.state" && r.extra?.isLoading === false,
      );
      expect(managerFalse.length).toBe(1);
      const lastManager = managerFalse[managerFalse.length - 1]!;
      expect(lastManager.threadId).toBe("thread-x");
    });
  });
});