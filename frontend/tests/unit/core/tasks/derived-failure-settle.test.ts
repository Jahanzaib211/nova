import { describe, expect, it } from "vitest";

import {
  DERIVED_FAILURE_SETTLE_MS,
  derivedFailureHasSettled,
} from "@/core/tasks/subtask-result";

/**
 * The subtask badge flashed red then green during normal, successful runs.
 *
 * Neither value was wrong. `derivePendingSubtaskStatus` concludes `failed` from
 * absence — no tool result, no active run — and it reads the runs cache, which
 * is a different channel from the task-event socket. The cache can report "no
 * pending run" a beat before the socket delivers `task_completed`. The FSM then
 * correctly lets the real event overturn the guess, so the user sees a failure
 * that never happened, followed by a success that did.
 *
 * A settle window is the smallest honest fix: it delays only a *guess*, never
 * evidence, and a genuine failure still paints once the window closes.
 */
describe("derivedFailureHasSettled", () => {
  it("does not paint a failure that has only just been observed", () => {
    const now = 1_000_000;
    expect(derivedFailureHasSettled(now, now)).toBe(false);
  });

  it("does not paint one still inside the window", () => {
    const since = 1_000_000;
    expect(
      derivedFailureHasSettled(since, since + DERIVED_FAILURE_SETTLE_MS - 1),
    ).toBe(false);
  });

  it("paints once the window closes — a real failure is not hidden", () => {
    const since = 1_000_000;
    expect(
      derivedFailureHasSettled(since, since + DERIVED_FAILURE_SETTLE_MS),
    ).toBe(true);
  });

  it("paints a long-standing failure immediately", () => {
    expect(derivedFailureHasSettled(1_000_000, 1_060_000)).toBe(true);
  });

  it("treats an unseen task as not settled", () => {
    expect(derivedFailureHasSettled(undefined, 1_000_000)).toBe(false);
  });

  it("honours an explicit window", () => {
    expect(derivedFailureHasSettled(0, 50, 100)).toBe(false);
    expect(derivedFailureHasSettled(0, 100, 100)).toBe(true);
  });

  it("keeps the window short enough to be invisible", () => {
    // Long enough to cover the two channels' skew, short enough that a real
    // failure is not perceptibly late.
    expect(DERIVED_FAILURE_SETTLE_MS).toBeGreaterThanOrEqual(200);
    expect(DERIVED_FAILURE_SETTLE_MS).toBeLessThanOrEqual(1000);
  });
});
