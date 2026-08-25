import { describe, expect, it } from "vitest";

import {
  classifyToolWork,
  isActivityTool,
  isEditorTool,
  isTerminalTool,
} from "@/core/threads/tool-surface";

/**
 * The Terminal and Activity tabs render a partition of one event stream, and
 * that partition has now drifted twice — silently, because a misclassified
 * event goes to the *other* tab rather than disappearing, so nothing errors.
 *
 * 2026-08-14: write_file/str_replace/read_file missing → Terminal empty for
 *             file-only runs.
 * 2026-08-21: the whole shell_* family missing → Terminal empty for any agent
 *             using the modern AIO execution path, while the backend stream
 *             was healthy. Reported as "terminal UI broken".
 */
describe("tool surface partition", () => {
  const LIVE_TOOL_NAMES = [
    // Builtins, from /api/runtime/capabilities on a live gateway.
    "agent_notify",
    "ask_clarification",
    "browser_check",
    "browser_click",
    "browser_eval",
    "browser_input",
    "browser_navigate",
    "code_review",
    "deploy_expose",
    "dev_verify",
    "free_port",
    "grep_files",
    "present_files",
    "save_skill",
    "scaffold_project",
    "screenshot",
    "search_files",
    "shell_kill",
    "shell_session",
    "shell_view",
    "shell_wait",
    "shell_write",
    "start_dev_server",
    "stop_dev_server",
    "system_probe",
    // Config-defined tools the agent also binds.
    "bash",
    "ls",
    "read_file",
    "glob",
    "grep",
    "write_file",
    "str_replace",
    "web_search",
    "web_fetch",
    "image_search",
    "task",
    "view_image",
  ];

  it("classifies every live tool into exactly one surface", () => {
    for (const name of LIVE_TOOL_NAMES) {
      expect(
        isTerminalTool(name) !== isActivityTool(name),
        `${name} must belong to exactly one tab`,
      ).toBe(true);
    }
  });

  it("routes the whole shell_* family to Terminal", () => {
    // The 2026-08-21 regression. The prefix rule means a shell_* tool added
    // tomorrow is handled without touching this file.
    for (const name of [
      "shell_session",
      "shell_view",
      "shell_wait",
      "shell_write",
      "shell_kill",
      "shell_something_new",
    ]) {
      expect(isTerminalTool(name), `${name} belongs in Terminal`).toBe(true);
    }
  });

  it("routes command and file work to Terminal", () => {
    for (const name of [
      "bash",
      "execute_command",
      "ls",
      "read_file",
      "write_file",
      "str_replace",
      "search_files",
      "grep_files",
      "glob",
      "grep",
    ]) {
      expect(isTerminalTool(name), `${name} belongs in Terminal`).toBe(true);
    }
  });

  it("leaves high-signal cards in Activity", () => {
    // These render as rich cards, not as a command and its output.
    for (const name of [
      "task",
      "browser_navigate",
      "browser_click",
      "screenshot",
      "code_review",
      "present_files",
      "ask_clarification",
      "start_dev_server",
    ]) {
      expect(isActivityTool(name), `${name} belongs in Activity`).toBe(true);
    }
  });

  it("does not match a prefix appearing mid-name", () => {
    // Guards against a lazy `includes` rewrite of the prefix rule.
    expect(isTerminalTool("my_shell_helper")).toBe(false);
  });
});

describe("editor focus", () => {
  it("focuses the Editor for writes, edits and scaffolding", () => {
    for (const name of ["write_file", "str_replace", "scaffold_project"]) {
      expect(isEditorTool(name), `${name} should focus Editor`).toBe(true);
    }
  });

  it("does not focus Editor for terminal-only or unknown tools", () => {
    for (const name of ["bash", "read_file", "shell_session", "task", "ls"]) {
      expect(isEditorTool(name), `${name} should not focus Editor`).toBe(false);
    }
  });

  it("never focuses Editor for a partial tool call", () => {
    expect(
      isEditorTool(undefined as unknown as string),
    ).toBe(false);
  });

  it("keeps editor tools on the Terminal surface", () => {
    // Focus is orthogonal to the partition: an editor write's output still
    // logs to Terminal once done.
    for (const name of ["write_file", "str_replace"]) {
      expect(isTerminalTool(name), `${name} stays Terminal-surface`).toBe(true);
    }
  });
});

describe("work kinds", () => {
  it("classifies known tools by their work", () => {
    expect(classifyToolWork("bash")).toBe("terminal");
    expect(classifyToolWork("shell_session")).toBe("terminal");
    expect(classifyToolWork("ls")).toBe("terminal");
    expect(classifyToolWork("read_file")).toBe("file-read");
    expect(classifyToolWork("write_file")).toBe("file-write");
    expect(classifyToolWork("str_replace")).toBe("file-edit");
    expect(classifyToolWork("search_files")).toBe("file-search");
    expect(classifyToolWork("glob")).toBe("file-search");
    expect(classifyToolWork("grep_files")).toBe("content-search");
    expect(classifyToolWork("grep")).toBe("content-search");
    expect(classifyToolWork("start_dev_server")).toBe("devserver");
    expect(classifyToolWork("task")).toBe("subagent");
    expect(classifyToolWork("scaffold_project")).toBe("scaffold");
    expect(classifyToolWork("browser_navigate")).toBe("browser");
    expect(classifyToolWork("web_search")).toBe("browser");
    expect(classifyToolWork("screenshot")).toBe("browser");
  });

  it("classifies future family members without code changes here", () => {
    expect(classifyToolWork("shell_repl_new")).toBe("terminal");
    expect(classifyToolWork("browser_something_new")).toBe("browser");
  });

  it("stays graceful on non-string names", () => {
    expect(classifyToolWork(undefined as unknown as string)).toBe("other");
    expect(classifyToolWork(null as unknown as string)).toBe("other");
    expect(classifyToolWork("")).toBe("other");
  });

  it("does not label every *search* word as browser work", () => {
    // A loose `includes("search")` heuristic would misroute hypothetical
    // tools like "research_topic" into the browser bucket.
    expect(classifyToolWork("search_workspace")).not.toBe("browser");
  });
});

describe("partial tool calls", () => {
  // A streaming tool call has no `name` until enough deltas arrive. The list
  // this module replaced was read via `Set.has()`, which tolerates that;
  // the prefix rule introduced an unguarded `.startsWith()` that threw and
  // unmounted the Agent's Computer behind its error boundary.
  const notNames = [undefined, null, "", 0, {}] as unknown[];

  it.each(notNames)("does not throw on a missing name: %p", (value) => {
    expect(() => isTerminalTool(value as string)).not.toThrow();
    expect(() => isActivityTool(value as string)).not.toThrow();
  });

  it("routes an unnamed call to Activity, not Terminal", () => {
    expect(isTerminalTool(undefined as unknown as string)).toBe(false);
    expect(isActivityTool(undefined as unknown as string)).toBe(true);
  });
});
