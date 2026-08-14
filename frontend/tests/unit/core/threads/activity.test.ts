import type { Message } from "@langchain/langgraph-sdk";
import { describe, expect, test } from "vitest";

import {
  activeWriteFilePathFromActivity,
  buildActivitySummary,
  currentToolFromActivity,
  messagesToActivityEvents,
} from "@/core/threads/activity";

// Minimal fixture builders mirroring the shapes the message stream delivers.
function ai(toolCalls: Array<{ id: string; name: string; args?: object }>) {
  return {
    id: `ai-${toolCalls.map((c) => c.id).join("-")}`,
    type: "ai",
    content: "",
    tool_calls: toolCalls.map((c) => ({ ...c, args: c.args ?? {} })),
  } as unknown as Message;
}

function tool(
  toolCallId: string,
  content: string,
  status?: "success" | "error",
) {
  return {
    id: `tool-${toolCallId}`,
    type: "tool",
    content,
    tool_call_id: toolCallId,
    ...(status ? { status } : {}),
  } as unknown as Message;
}

describe("messagesToActivityEvents", () => {
  test("a tool_call with no result yet is 'running'", () => {
    const events = messagesToActivityEvents([
      ai([{ id: "t1", name: "bash", args: { command: "npm install" } }]),
    ]);
    expect(events).toHaveLength(1);
    expect(events[0]).toMatchObject({
      id: "t1",
      type: "bash",
      status: "running",
      summary: "$ npm install",
      output: "",
    });
  });

  test("a tool_call with a result collapses to one 'done' event", () => {
    const events = messagesToActivityEvents([
      ai([{ id: "t1", name: "bash", args: { command: "echo hi" } }]),
      tool("t1", "hi"),
    ]);
    expect(events).toHaveLength(1);
    expect(events[0]).toMatchObject({
      id: "t1",
      status: "done",
      output: "hi",
    });
  });

  test("write_file carries its path and Editor summary", () => {
    const events = messagesToActivityEvents([
      ai([
        {
          id: "w1",
          name: "write_file",
          args: {
            path: "/mnt/user-data/workspace/index.html",
            content: "<html>",
          },
        },
      ]),
    ]);
    expect(events[0]).toMatchObject({
      type: "write_file",
      path: "/mnt/user-data/workspace/index.html",
      summary: "Writing index.html",
      status: "running",
    });
  });

  test("an error ToolMessage yields status 'error'", () => {
    const events = messagesToActivityEvents([
      ai([{ id: "t1", name: "bash", args: { command: "false" } }]),
      tool("t1", "boom", "error"),
    ]);
    expect(events[0]?.status).toBe("error");
  });

  // The next four tests pin the 2026-08-14 fix: many sandbox tools legitimately
  // return ``f"Error: …"`` strings on success paths. The heuristic that
  // marked every "Error:"-prefixed result as an error painted the Activity
  // tab red mid-task; we now trust ``tm.status`` only.

  test("an 'Error:'-prefixed SUCCESS ToolMessage stays 'done'", () => {
    const events = messagesToActivityEvents([
      ai([{ id: "t1", name: "read_file", args: { path: "/x" } }]),
      tool("t1", "Error: no such file"), // status omitted → success
    ]);
    expect(events[0]?.status).toBe("done");
  });

  test("bash_tool's host-bash refusal (Error: …) stays 'done'", () => {
    const events = messagesToActivityEvents([
      ai([{ id: "t1", name: "bash", args: { command: "true" } }]),
      tool("t1", "Error: This host bash is disabled"),
    ]);
    expect(events[0]?.status).toBe("done");
  });

  test("search_files' 'Workspace not found' (Error: …) stays 'done'", () => {
    const events = messagesToActivityEvents([
      ai([{ id: "t1", name: "search_files", args: { pattern: "*.html" } }]),
      tool("t1", "Error: Workspace not found at /mnt/user-data/workspace"),
    ]);
    expect(events[0]?.status).toBe("done");
  });

  test("read_file's 'File not found' (Error: …) stays 'done'", () => {
    const events = messagesToActivityEvents([
      ai([{ id: "t1", name: "read_file", args: { path: "/missing" } }]),
      tool("t1", "Error: File not found: /missing"),
    ]);
    expect(events[0]?.status).toBe("done");
  });

  test("multiple tool calls across messages preserve order", () => {
    const events = messagesToActivityEvents([
      ai([{ id: "t1", name: "bash", args: { command: "ls" } }]),
      tool("t1", "a\nb"),
      ai([{ id: "w1", name: "write_file", args: { path: "/a.txt" } }]),
    ]);
    expect(events.map((e) => e.id)).toEqual(["t1", "w1"]);
    expect(events[0]?.status).toBe("done");
    expect(events[1]?.status).toBe("running");
  });

  test("respects the tail limit", () => {
    const msgs = Array.from({ length: 10 }, (_, i) =>
      ai([{ id: `t${i}`, name: "bash", args: { command: `cmd${i}` } }]),
    );
    const events = messagesToActivityEvents(msgs, { limit: 3 });
    expect(events).toHaveLength(3);
    expect(events.map((e) => e.id)).toEqual(["t7", "t8", "t9"]);
  });

  test("ignores non-ai / non-tool messages", () => {
    const events = messagesToActivityEvents([
      { id: "h1", type: "human", content: "build me a site" } as Message,
      ai([{ id: "t1", name: "bash", args: { command: "ls" } }]),
    ]);
    expect(events).toHaveLength(1);
  });
});

describe("currentToolFromActivity", () => {
  test("returns the last running tool", () => {
    const events = messagesToActivityEvents([
      ai([{ id: "t1", name: "bash", args: { command: "ls" } }]),
      tool("t1", "done"),
      ai([{ id: "w1", name: "write_file", args: { path: "/a.txt" } }]),
    ]);
    expect(currentToolFromActivity(events)).toBe("write_file");
  });

  test("returns null when everything is done (idle)", () => {
    const events = messagesToActivityEvents([
      ai([{ id: "t1", name: "bash", args: { command: "ls" } }]),
      tool("t1", "done"),
    ]);
    expect(currentToolFromActivity(events)).toBeNull();
  });
});

describe("activeWriteFilePathFromActivity", () => {
  test("returns the most recent write_file path", () => {
    const events = messagesToActivityEvents([
      ai([{ id: "w1", name: "write_file", args: { path: "/first.html" } }]),
      ai([{ id: "w2", name: "write_file", args: { path: "/second.html" } }]),
    ]);
    expect(activeWriteFilePathFromActivity(events)).toBe("/second.html");
  });

  test("ignores non-write tools", () => {
    const events = messagesToActivityEvents([
      ai([{ id: "t1", name: "bash", args: { command: "ls" } }]),
    ]);
    expect(activeWriteFilePathFromActivity(events)).toBeNull();
  });
});

describe("buildActivitySummary", () => {
  test("phrases each tool naturally", () => {
    expect(buildActivitySummary("write_file", { path: "/a/b.ts" })).toBe(
      "Writing b.ts",
    );
    expect(buildActivitySummary("str_replace", { path: "/a/b.ts" })).toBe(
      "Editing b.ts",
    );
    expect(buildActivitySummary("bash", { cmd: "npm run build" })).toBe(
      "$ npm run build",
    );
    expect(buildActivitySummary("grep_files", {})).toBe("Searching content");
    expect(buildActivitySummary("unknown_tool", {})).toBe("unknown_tool");
  });
});
