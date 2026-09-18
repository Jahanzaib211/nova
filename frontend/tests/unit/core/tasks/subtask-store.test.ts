/**
 * The subtask registry is an external store (see SubtaskStore's docstring for
 * the React #185 it replaced). These pin the two properties the fix relies
 * on: writes apply to the live state synchronously, and listeners fire only
 * when the reference actually changes.
 */
import { describe, expect, it, vi } from "vitest";

import { SubtaskStore } from "@/core/tasks/context";
import type { Subtask } from "@/core/tasks/types";

const task = (id: string, status: Subtask["status"]): Subtask => ({
  id,
  subagent_type: "general-purpose",
  description: "d",
  prompt: "p",
  status,
});

describe("SubtaskStore", () => {
  it("applies updates to the live state, not a captured base", () => {
    const store = new SubtaskStore();
    store.update((c) => ({ ...c, a: task("a", "in_progress") }));
    store.update((c) => ({ ...c, b: task("b", "in_progress") }));
    // Each updater saw the previous write: both keys survive.
    expect(Object.keys(store.getSnapshot()).sort()).toEqual(["a", "b"]);
  });

  it("returns a stable snapshot and notifies only on reference change", () => {
    const store = new SubtaskStore();
    const listener = vi.fn();
    store.subscribe(listener);
    const before = store.getSnapshot();
    store.update((c) => c); // identity: no notification, same snapshot
    expect(listener).not.toHaveBeenCalled();
    expect(store.getSnapshot()).toBe(before);
    store.update((c) => ({ ...c, a: task("a", "completed") }));
    expect(listener).toHaveBeenCalledTimes(1);
    expect(store.getSnapshot()).not.toBe(before);
  });

  it("unsubscribes", () => {
    const store = new SubtaskStore();
    const listener = vi.fn();
    const off = store.subscribe(listener);
    off();
    store.update((c) => ({ ...c, a: task("a", "failed") }));
    expect(listener).not.toHaveBeenCalled();
  });
});
