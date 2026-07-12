import { describe, expect, it } from "vitest";

import { activeRunPollInterval } from "@/core/threads/hooks";

describe("activeRunPollInterval — Phase 3 cadence helper", () => {
  it("returns false (paused) when no active run", () => {
    expect(
      activeRunPollInterval({ hasActiveRun: false, isStreamLoading: false }),
    ).toBe(false);
  });

  it("returns false when no active run even if SDK is loading", () => {
    expect(
      activeRunPollInterval({ hasActiveRun: false, isStreamLoading: true }),
    ).toBe(false);
  });

  it("returns 30s cadence when active run exists AND stream is loading", () => {
    expect(
      activeRunPollInterval({
        hasActiveRun: true,
        isStreamLoading: true,
      }),
    ).toBe(30_000);
  });

  it("returns 4s cadence when active run exists AND no live stream", () => {
    expect(
      activeRunPollInterval({
        hasActiveRun: true,
        isStreamLoading: false,
      }),
    ).toBe(4_000);
  });

  it("never disables polling while a run is active (the Phase 3 invariant)", () => {
    expect(
      activeRunPollInterval({ hasActiveRun: true, isStreamLoading: false }),
    ).not.toBe(false);
    expect(
      activeRunPollInterval({ hasActiveRun: true, isStreamLoading: true }),
    ).not.toBe(false);
  });
});
