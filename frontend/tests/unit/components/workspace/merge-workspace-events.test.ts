import { describe, expect, it } from "vitest";

import { mergeWorkspaceEvents } from "@/components/workspace/agent-computer/workspace-state";
import type { SandboxEvent } from "@/core/sandbox/hooks";
import type { AgentActivityEvent } from "@/core/threads/hooks";

/**
 * Two identity bugs in the Terminal, both silent and both intermittent.
 *
 * 1. React keys came from the array index (`log-${i}-${e.ts}`). Once the
 *    200-event window rolls, every element shifts by one, so every key changes
 *    and React remounts the whole list mid-stream.
 * 2. The de-dupe against in-flight events used a Set keyed on
 *    `ts|type|summary`, where `ts` is `"%H:%M:%S"`. Two identical commands in
 *    the same second collapse to one key, so one log line silently masked both
 *    running events.
 */

function logEvent(over: Partial<SandboxEvent> = {}): SandboxEvent {
  return {
    ts: "14:23:01",
    type: "bash",
    path: null,
    summary: "$ echo hi",
    output: "hi",
    ...over,
  };
}

function runningEvent(
  over: Partial<AgentActivityEvent> = {},
): AgentActivityEvent {
  return {
    id: "act-1",
    ts: "14:23:01",
    type: "bash",
    path: null,
    summary: "$ echo hi",
    output: "",
    status: "running",
    ...over,
  } as AgentActivityEvent;
}

describe("React identity", () => {
  it("keys on the ingest-time uid, not the array position", () => {
    const events = [
      logEvent({ uid: "sbx-7", summary: "$ first" }),
      logEvent({ uid: "sbx-8", summary: "$ second" }),
    ];
    const merged = mergeWorkspaceEvents(events, []);
    expect(merged.map((e) => e.id)).toEqual(["sbx-7", "sbx-8"]);

    // Drop the head, as the rolling window does. The surviving element must
    // keep the key it already had.
    const rolled = mergeWorkspaceEvents(events.slice(1), []);
    expect(rolled[0]!.id).toBe("sbx-8");
  });

  it("falls back to a content key for a line with no uid", () => {
    const merged = mergeWorkspaceEvents([logEvent()], []);
    expect(merged[0]!.id).toBe("14:23:01-bash-$ echo hi");
  });
});

describe("streamed commands show as running", () => {
  it("keeps a running state until the closing frame", () => {
    const merged = mergeWorkspaceEvents(
      [logEvent({ uid: "a", state: "running" })],
      [],
    );
    expect(merged[0]!.status).toBe("running");
  });

  it("marks a finished command done", () => {
    const merged = mergeWorkspaceEvents(
      [logEvent({ uid: "a", state: "done" })],
      [],
    );
    expect(merged[0]!.status).toBe("done");
  });

  it("treats a plain non-streaming line as done", () => {
    const merged = mergeWorkspaceEvents([logEvent({ uid: "a" })], []);
    expect(merged[0]!.status).toBe("done");
  });
});

describe("de-duplication against in-flight events", () => {
  it("hides a running event the log already covers", () => {
    const merged = mergeWorkspaceEvents(
      [logEvent({ uid: "a" })],
      [runningEvent()],
    );
    expect(merged).toHaveLength(1);
    expect(merged[0]!.id).toBe("a");
  });

  it("keeps a running event the log does not cover", () => {
    const merged = mergeWorkspaceEvents(
      [logEvent({ uid: "a", summary: "$ something else" })],
      [runningEvent()],
    );
    expect(merged).toHaveLength(2);
  });

  it("masks exactly as many running events as there are log lines", () => {
    // The bug: two identical commands in the same second. One log line arrived
    // first; a Set-based check hid *both* running events, so the second command
    // vanished from the Terminal until its own log line landed.
    const merged = mergeWorkspaceEvents(
      [logEvent({ uid: "a" })],
      [runningEvent({ id: "act-1" }), runningEvent({ id: "act-2" })],
    );
    expect(merged).toHaveLength(2);
    expect(merged.map((e) => e.id)).toEqual(["a", "act-2"]);
  });

  it("masks both when both have landed in the log", () => {
    const merged = mergeWorkspaceEvents(
      [logEvent({ uid: "a" }), logEvent({ uid: "b" })],
      [runningEvent({ id: "act-1" }), runningEvent({ id: "act-2" })],
    );
    expect(merged.map((e) => e.id)).toEqual(["a", "b"]);
  });

  it("ignores non-running activity events entirely", () => {
    const merged = mergeWorkspaceEvents(
      [],
      [runningEvent({ id: "act-1", status: "done" })],
    );
    expect(merged).toHaveLength(0);
  });
});
