import { describe, expect, it } from "vitest";

import {
  isValidTeardownReason,
  TEARDOWN_REASONS,
  type TeardownReason,
} from "@/core/threads/stream-trace";

describe("TEARDOWN_REASONS — exhaustive inventory of teardown triggers", () => {
  it("contains every documented trigger", () => {
    const expected: TeardownReason[] = [
      "watchdog-force-stop",
      "watchdog-converge-idle",
      "stop-timeout-forced",
      "force-disconnect",
      "manual-stop",
      "unmount",
      "thread-switch",
      "logout",
      "reset",
    ];
    expect([...TEARDOWN_REASONS].sort()).toEqual([...expected].sort());
  });
});

describe("isValidTeardownReason — caller funnel guard", () => {
  it("accepts every documented reason", () => {
    for (const r of TEARDOWN_REASONS) {
      expect(isValidTeardownReason(r)).toBe(true);
    }
  });

  it("rejects unknown reasons (caller must extend the union)", () => {
    expect(isValidTeardownReason("unknown-reason")).toBe(false);
    expect(isValidTeardownReason("")).toBe(false);
    expect(isValidTeardownReason("Watchdog-Force-Stop")).toBe(false);
  });
});
