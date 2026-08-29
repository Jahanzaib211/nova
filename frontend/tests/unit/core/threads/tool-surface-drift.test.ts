import { describe, expect, it } from "vitest";

import {
  isActivityTool,
  isTerminalTool,
} from "@/core/threads/tool-surface";

/**
 * Drift guard: if someone adds a new tool and forgets to update the
 * classifier, the misclassified event lands in the wrong tab silently
 * (nothing errors). This test catches the two most common regressions:
 *
 *   1. A new shell-family tool added without the prefix rule picking it up.
 *   2. A new activity-only tool accidentally routed to Terminal.
 *
 * This is intentionally separate from tool-surface.test.ts which tests
 * the full partition; this file is the minimal "known-tools snapshot" that
 * fails fast when the set drifts.
 */
describe("tool surface drift guard", () => {
  const KNOWN_TERMINAL_TOOLS = [
    "bash",
    "execute_command",
    "ls",
    "glob",
    "grep",
    "read_file",
    "write_file",
    "str_replace",
    "search_files",
    "grep_files",
    "shell_session",
    "shell_view",
    "shell_wait",
    "shell_write",
    "shell_kill",
  ];

  const KNOWN_ACTIVITY_TOOLS = [
    "present_files",
    "ask_clarification",
    "browser_check",
    "browser_click",
    "browser_eval",
    "browser_input",
    "browser_navigate",
    "task",
    "screenshot",
    "code_review",
    "start_dev_server",
    "stop_dev_server",
    "deploy_expose",
    "scaffold_project",
    "web_search",
    "web_fetch",
    "image_search",
    "view_image",
  ];

  it("all known terminal tools are classified as terminal", () => {
    for (const name of KNOWN_TERMINAL_TOOLS) {
      expect(isTerminalTool(name), `${name} should be terminal`).toBe(true);
      expect(isActivityTool(name), `${name} should not be activity`).toBe(false);
    }
  });

  it("all known activity tools are classified as activity", () => {
    for (const name of KNOWN_ACTIVITY_TOOLS) {
      expect(isActivityTool(name), `${name} should be activity`).toBe(true);
      expect(isTerminalTool(name), `${name} should not be terminal`).toBe(
        false,
      );
    }
  });

  it("the shell_* prefix catches new family members", () => {
    expect(isTerminalTool("shell_repl")).toBe(true);
    expect(isTerminalTool("shell_exec")).toBe(true);
    expect(isTerminalTool("shell_custom")).toBe(true);
  });

  it("the browser_* prefix stays in activity", () => {
    expect(isActivityTool("browser_new_thing")).toBe(true);
    expect(isTerminalTool("browser_new_thing")).toBe(false);
  });
});
