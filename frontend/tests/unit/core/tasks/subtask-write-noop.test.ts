/**
 * MessageList flushes its render-derived subtask writes in an effect on every
 * render, trusting `updateSubtask` to hand back the same state reference for
 * a no-op. Under load (2026-09-18, thread with an orphaned `task` call), the
 * functional updater kept seeing a stale base (`in_progress`) while the
 * provider already rendered `failed`, so the same accepted transition was
 * re-issued on every render until React threw #185 and the route boundary
 * replaced the whole workspace. The flush now skips writes the rendered
 * provider state already reflects, which breaks the cycle regardless of what
 * base the updater is applied to.
 */
import { describe, expect, it } from "vitest";

import { subtaskWriteIsNoop } from "@/core/tasks/context";
import type { Subtask } from "@/core/tasks/types";

const existing: Subtask = {
  id: "c1",
  subagent_type: "general-purpose",
  description: "D",
  prompt: "P",
  status: "failed",
  error: "No result recorded",
};

describe("subtaskWriteIsNoop", () => {
  it("is a no-op when every written field matches the rendered task", () => {
    expect(
      subtaskWriteIsNoop(existing, {
        id: "c1",
        subagent_type: "general-purpose",
        description: "D",
        prompt: "P",
        status: "failed",
        error: "No result recorded",
      }),
    ).toBe(true);
  });

  it("is a no-op for a partial write whose fields all match", () => {
    expect(subtaskWriteIsNoop(existing, { id: "c1", status: "failed" })).toBe(
      true,
    );
  });

  it("is not a no-op when the task is unknown", () => {
    expect(subtaskWriteIsNoop(undefined, { id: "c1", status: "failed" })).toBe(
      false,
    );
  });

  it("is not a no-op when any observable field differs", () => {
    expect(
      subtaskWriteIsNoop(existing, { id: "c1", status: "in_progress" }),
    ).toBe(false);
    expect(
      subtaskWriteIsNoop(existing, { id: "c1", status: "failed", error: "x" }),
    ).toBe(false);
    expect(subtaskWriteIsNoop(existing, { id: "c1", result: "done" })).toBe(
      false,
    );
    expect(
      subtaskWriteIsNoop(existing, {
        id: "c1",
        latestMessage: {
          type: "ai",
          content: "hi",
        } as Subtask["latestMessage"],
      }),
    ).toBe(false);
  });
});
