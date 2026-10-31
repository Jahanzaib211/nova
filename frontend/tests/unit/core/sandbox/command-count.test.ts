import { describe, expect, it } from "vitest";

import { foldCommandCount } from "@/core/sandbox/command-count";

describe("foldCommandCount", () => {
  it("takes the first real observation", () => {
    expect(foldCommandCount(undefined, 12)).toBe(12);
  });

  it("tracks the backend upward", () => {
    expect(foldCommandCount(313, 314)).toBe(314);
  });

  it("ignores a replayed historical frame", () => {
    // The computer-ws hub replays its buffer on every reconnect; applying a
    // stale 146 over a live 313 is what made the header flicker.
    expect(foldCommandCount(313, 146)).toBe(313);
  });

  it("is idempotent, so a full replay changes nothing", () => {
    const replay = [146, 200, 260, 313];
    let n: number | undefined = 313;
    for (const frame of replay) n = foldCommandCount(n, frame);
    expect(n).toBe(313);
  });

  it("keeps the current value when the count is unknown", () => {
    // `null` is the endpoint's "unknown" for a thread with no counter yet.
    expect(foldCommandCount(313, null)).toBe(313);
    expect(foldCommandCount(313, undefined)).toBe(313);
  });

  it("stays undefined while nothing is known, so the header shows ~N", () => {
    expect(foldCommandCount(undefined, null)).toBeUndefined();
  });

  it("rejects a non-finite count rather than poisoning the header", () => {
    expect(foldCommandCount(313, Number.NaN)).toBe(313);
  });
});
