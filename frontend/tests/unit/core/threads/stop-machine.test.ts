import { describe, expect, it } from "vitest";

import {
  composerShouldStream,
  nextStopState,
} from "@/core/threads/stream-trace";

describe("nextStopState — bounded stop + force-disconnect state machine", () => {
  it("idle + stop → stopping", () => {
    const r = nextStopState("idle", "stop", 0, false);
    expect(r.next).toBe("stopping");
    expect(r.outcome.kind).toBe("stopped");
  });

  it("idle + force → force-disconnected immediately (user escape hatch)", () => {
    const r = nextStopState("idle", "force", 0, false);
    expect(r.next).toBe("force-disconnected");
    expect(r.outcome.kind).toBe("force-disconnected");
    if (r.outcome.kind === "force-disconnected") {
      expect(r.outcome.reason).toBe("user-force");
    }
  });

  it("stopping + tick before timeout → stopping", () => {
    const r = nextStopState("stopping", "tick", 2_000, false);
    expect(r.next).toBe("stopping");
  });

  it("stopping + tick after timeout with no ack → force-disconnected (stop-timeout)", () => {
    const r = nextStopState("stopping", "tick", 5_000, false);
    expect(r.next).toBe("force-disconnected");
    expect(r.outcome.kind).toBe("force-disconnected");
    if (r.outcome.kind === "force-disconnected") {
      expect(r.outcome.reason).toBe("stop-timeout");
    }
  });

  it("stopping + tick with server ack → stopped", () => {
    const r = nextStopState("stopping", "tick", 1_000, true);
    expect(r.next).toBe("stopped");
    expect(r.outcome.kind).toBe("stopped");
  });

  it("tick on idle is a noop (not-loading)", () => {
    const r = nextStopState("idle", "tick", 1_000, true);
    expect(r.next).toBe("idle");
    expect(r.outcome.kind).toBe("noop");
  });

  it("escape from any state → idle", () => {
    expect(nextStopState("stopping", "escape", 0, false).next).toBe("idle");
    expect(nextStopState("stopped", "escape", 0, false).next).toBe("idle");
    expect(nextStopState("force-disconnected", "escape", 0, false).next).toBe(
      "idle",
    );
  });
});

describe("composerShouldStream — composer escape hatch", () => {
  it("thread error wins over all other signals", () => {
    expect(
      composerShouldStream({
        threadIsLoading: true,
        hasActiveRun: true,
        activeRunId: "r1",
        dismissedRunId: null,
        threadError: new Error("boom"),
      }),
    ).toBe("error");
  });

  it("thread idle + no active run → ready", () => {
    expect(
      composerShouldStream({
        threadIsLoading: false,
        hasActiveRun: false,
        activeRunId: null,
        dismissedRunId: null,
        threadError: null,
      }),
    ).toBe("ready");
  });

  it("thread loading → streaming", () => {
    expect(
      composerShouldStream({
        threadIsLoading: true,
        hasActiveRun: true,
        activeRunId: "r1",
        dismissedRunId: null,
        threadError: null,
      }),
    ).toBe("streaming");
  });

  it("active run exists, dismissedRunId matches → ready (escape hatch)", () => {
    expect(
      composerShouldStream({
        threadIsLoading: true,
        hasActiveRun: true,
        activeRunId: "r1",
        dismissedRunId: "r1",
        threadError: null,
      }),
    ).toBe("ready");
  });

  it("active run exists, dismissedRunId is a DIFFERENT run → still streaming", () => {
    expect(
      composerShouldStream({
        threadIsLoading: true,
        hasActiveRun: true,
        activeRunId: "r2",
        dismissedRunId: "r1",
        threadError: null,
      }),
    ).toBe("streaming");
  });

  it("active run exists, dismissedRunId set but no current activeRun → streaming", () => {
    expect(
      composerShouldStream({
        threadIsLoading: true,
        hasActiveRun: false,
        activeRunId: null,
        dismissedRunId: "r1",
        threadError: null,
      }),
    ).toBe("streaming");
  });
});
