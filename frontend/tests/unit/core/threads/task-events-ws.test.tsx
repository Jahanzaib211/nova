import { describe, expect, it } from "vitest";

import { SUPERSEDE_GRACE_MS, completedTodoIndexes } from "@/core/tasks/context";
import {
  applyTaskEvent,
  authProbeVerdict,
  computerWsUrl,
  isTaskLifecycleEvent,
} from "@/core/threads/task-events-ws";

describe("isTaskLifecycleEvent", () => {
  it("accepts the six lifecycle types and rejects siblings", () => {
    expect(isTaskLifecycleEvent({ type: "task_started", task_id: "t" })).toBe(
      true,
    );
    expect(isTaskLifecycleEvent({ type: "task_timed_out", task_id: "t" })).toBe(
      true,
    );
    // Sibling task-prefixed events with different shapes are not ours.
    expect(isTaskLifecycleEvent({ type: "task_progress", step: 1 })).toBe(
      false,
    );
    expect(isTaskLifecycleEvent({ type: "verify_result", ok: true })).toBe(
      false,
    );
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
      {
        type: "task_completed",
        task_id: "t1",
        result: "ok",
        todo_indexes: [2],
      },
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

describe("computerWsUrl", () => {
  it("targets computer-ws, with no /runs/ segment and no tasks-ws", () => {
    const url = computerWsUrl("abc-123", "https://nova.example.com");
    // nginx matches ^/api/threads/[^/]+/computer-ws$ exactly. When the client
    // moved to this route without a matching proxy location, every handshake
    // fell through to the generic /api/threads block, which sets no Upgrade
    // header — the subagent panel went dark and nothing said why.
    expect(url).toBe("wss://nova.example.com/api/threads/abc-123/computer-ws");
    expect(url).not.toContain("/runs/");
    expect(url).not.toContain("tasks-ws");
  });

  it("upgrades the scheme rather than assuming one", () => {
    expect(computerWsUrl("t", "http://localhost:2026")).toBe(
      "ws://localhost:2026/api/threads/t/computer-ws",
    );
  });

  it("escapes thread ids so a crafted id cannot alter the path", () => {
    expect(computerWsUrl("a/b", "http://x")).toContain("a%2Fb");
  });
});

describe("authProbeVerdict", () => {
  it("treats only 401 as terminal", () => {
    // A refused handshake carries no status to script, so this verdict is the
    // only thing standing between "tell the user to reload" and a tab that
    // retries a dead session every 18 seconds forever, saying nothing.
    expect(authProbeVerdict(401)).toBe("expired");
  });

  it("keeps retrying the benign first-mount race", () => {
    // The panel opens its socket before the thread's metadata row exists, so
    // 403/404 here is ordinary startup, not a reason to give up.
    expect(authProbeVerdict(403)).toBe("retry");
    expect(authProbeVerdict(404)).toBe("retry");
    expect(authProbeVerdict(200)).toBe("retry");
    expect(authProbeVerdict(500)).toBe("retry");
  });
});

describe("supersede grace window", () => {
  it("protects a subtask that the message stream has not caught up to", () => {
    // The socket and the SSE message stream are independent, so task_started
    // routinely arrives before the assistant message carrying its tool_call.
    // Superseding on that gap fails a subagent that had only just started —
    // which is precisely what a user sees as "subagents are broken".
    const now = Date.now();
    const justArrived = { status: "in_progress", firstSeenAt: now - 500 };
    const isLive = false;
    const tooYoung =
      justArrived.firstSeenAt !== undefined &&
      now - justArrived.firstSeenAt < SUPERSEDE_GRACE_MS;
    expect(justArrived.status === "in_progress" && !isLive && !tooYoung).toBe(
      false,
    );
  });

  it("still settles a genuinely orphaned subtask once the window passes", () => {
    const now = Date.now();
    const orphan = {
      status: "in_progress",
      firstSeenAt: now - SUPERSEDE_GRACE_MS - 1,
    };
    const isLive = false;
    const tooYoung =
      orphan.firstSeenAt !== undefined &&
      now - orphan.firstSeenAt < SUPERSEDE_GRACE_MS;
    expect(orphan.status === "in_progress" && !isLive && !tooYoung).toBe(true);
  });
});
