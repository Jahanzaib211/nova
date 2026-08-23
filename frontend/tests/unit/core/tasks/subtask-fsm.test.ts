import { describe, expect, it } from "vitest";

import { nextSubtaskStatus } from "@/core/tasks/context";

/**
 * Explicit FSM for subtask status (2026-07-12 subtask-sync RCA).
 *
 * Allowed:
 *   in_progress → completed | failed          (any source)
 *   derived terminal → result terminal        (real ToolMessage corrects a guess)
 * Ignored:
 *   terminal → in_progress                    (regression)
 *   result terminal → anything                (backend emits exactly one terminal state)
 */
describe("nextSubtaskStatus", () => {
  it("allows in_progress → completed", () => {
    expect(
      nextSubtaskStatus("in_progress", "completed", "derived", "result"),
    ).toEqual({ status: "completed", accepted: true });
  });

  it("allows in_progress → failed", () => {
    expect(
      nextSubtaskStatus("in_progress", "failed", "derived", "derived"),
    ).toEqual({ status: "failed", accepted: true });
  });

  it("allows undefined → any incoming status", () => {
    expect(
      nextSubtaskStatus(undefined, "in_progress", undefined, "derived"),
    ).toEqual({ status: "in_progress", accepted: true });
  });

  it("ignores terminal → in_progress regression (stuck-Running bug)", () => {
    // Regression: a later run on the same thread made hasActiveRun true and
    // re-derived old completed tasks as in_progress on every render.
    expect(
      nextSubtaskStatus("completed", "in_progress", "result", "derived"),
    ).toEqual({ status: "completed", accepted: false });
    expect(
      nextSubtaskStatus("failed", "in_progress", "result", "derived"),
    ).toEqual({ status: "failed", accepted: false });
  });

  it("will not let a guess overwrite a NON-terminal backend report", () => {
    // Regression: run b236208c — three subagents completed and the run
    // recorded success, while the UI showed "Subtask failed" throughout.
    //
    // Every other case here has a terminal `previous`, and the old rule only
    // protected terminal states — so a streamed `in_progress` from
    // task_running was still clobbered by the derived "no active run ->
    // failed" pass on the next render. That is the shape that painted a
    // healthy, actively-streaming subagent red.
    expect(
      nextSubtaskStatus("in_progress", "failed", "result", "derived"),
    ).toEqual({ status: "in_progress", accepted: false });

    // The same guard must not block a real backend terminal state.
    expect(
      nextSubtaskStatus("in_progress", "completed", "result", "result"),
    ).toEqual({ status: "completed", accepted: true });

    // And a derived update is still fine when nothing authoritative exists.
    expect(
      nextSubtaskStatus("in_progress", "failed", "derived", "derived"),
    ).toEqual({ status: "failed", accepted: true });
  });

  it("lets a real ToolMessage correct a derived failed guess (false-Failed bug)", () => {
    // Regression: run 01984062 — stream dropped, UI derived "failed"; the
    // ToolMessage later replayed with "Task Succeeded".
    expect(
      nextSubtaskStatus("failed", "completed", "derived", "result"),
    ).toEqual({ status: "completed", accepted: true });
  });

  it("keeps result-sourced terminal states immutable", () => {
    // Backend emits exactly one terminal state per task; a second
    // conflicting terminal claim is invalid and ignored.
    expect(
      nextSubtaskStatus("completed", "failed", "result", "result"),
    ).toEqual({ status: "completed", accepted: false });
    expect(
      nextSubtaskStatus("completed", "failed", "result", "derived"),
    ).toEqual({ status: "completed", accepted: false });
  });

  it("treats a repeated identical status as accepted and stable", () => {
    expect(
      nextSubtaskStatus("completed", "completed", "result", "result"),
    ).toEqual({ status: "completed", accepted: true });
  });

  it("keeps previous status when incoming is undefined (field-only merge)", () => {
    expect(
      nextSubtaskStatus("in_progress", undefined, "derived", "derived"),
    ).toEqual({ status: "in_progress", accepted: false });
  });
});
