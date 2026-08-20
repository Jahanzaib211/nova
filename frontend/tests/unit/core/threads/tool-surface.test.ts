import { describe, expect, it } from "vitest";

import { isActivityTool, isTerminalTool } from "@/core/threads/tool-surface";

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
      "read_file",
      "write_file",
      "str_replace",
      "search_files",
      "grep_files",
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
