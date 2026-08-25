import { describe, expect, it } from "vitest";

import { completedTodoIndexes } from "@/core/tasks/context";
import {
  applyTaskEvent,
  isTaskLifecycleEvent,
} from "@/core/threads/task-events-ws";

describe("isTaskLifecycleEvent", () => {
  it("accepts the six lifecycle types and rejects siblings", () => {
    expect(
      isTaskLifecycleEvent({ type: "task_started", task_id: "t" }),
    ).toBe(true);
    expect(
      isTaskLifecycleEvent({ type: "task_timed_out", task_id: "t" }),
    ).toBe(true);
    // Sibling task-prefixed events with different shapes are not ours.
    expect(isTaskLifecycleEvent({ type: "task_progress", step: 1 })).toBe(false);
    expect(isTaskLifecycleEvent({ type: "verify_result", ok: true })).toBe(false);
    expect(isTaskLifecycleEvent("junk")).toBe(false);
  });
});

describe("applyTaskEvent", () => {
  it("carries todo_indexes onto the stored subtask", () => {
    const patches: unknown[] = [];
    applyTaskEvent(
      { type: "task_started", task_id: "t1", todo_indexes: [2] },
      (patch) => patches.push(patch),
    );
    applyTaskEvent(
      { type: "task_completed", task_id: "t1", result: "ok", todo_indexes: [2] },
      (patch) => patches.push(patch),
    );
    expect(patches[0]).toMatchObject({
      id: "t1",
      status: "in_progress",
      todoIndexes: [2],
    });
    expect(patches[1]).toMatchObject({
      id: "t1",
      status: "completed",
      todoIndexes: [2],
    });
  });

  it("treats a malformed binding as absent, not as an empty strike set", () => {
    const patches: unknown[] = [];
    applyTaskEvent(
      { type: "task_completed", task_id: "t1", todo_indexes: "2" as never },
      (patch) => patches.push(patch),
    );
    expect(patches[0]).not.toHaveProperty("todoIndexes");
  });
});

describe("completedTodoIndexes", () => {
  it("unions indexes of completed tasks only — failed bindings strike nothing", () => {
    const bindings = completedTodoIndexes({
      a: {
        id: "a",
        status: "failed",
        subagent_type: "general-purpose",
        description: "d",
        prompt: "p",
        todoIndexes: [0],
      },
      b: {
        id: "b",
        status: "completed",
        subagent_type: "general-purpose",
        description: "d",
        prompt: "p",
        todoIndexes: [2, 5],
      },
    });
    expect(bindings.has(2)).toBe(true);
    expect(bindings.has(5)).toBe(true);
    // A failed subagent must not strike its rows even though it carries
    // bindings — out-of-order completion was exactly the old bug.
    expect(bindings.has(0)).toBe(false);
  });

  it("returns an empty set for tasks without bindings", () => {
    const bindings = completedTodoIndexes({
      a: {
        id: "a",
        status: "completed",
        subagent_type: "general-purpose",
        description: "d",
        prompt: "p",
      },
    });
    expect(bindings.size).toBe(0);
  });
});
