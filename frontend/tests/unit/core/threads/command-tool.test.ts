import { describe, expect, it } from "vitest";

import { isCommandTool, isTerminalTool } from "@/core/threads/tool-surface";

/**
 * The Terminal header shows the gateway's deterministic total when the
 * computer-ws socket is up, and falls back to counting local events when it is
 * not. Those branches used to count different populations — the backend counted
 * commands, the fallback counted every terminal-surface event — so on a live
 * thread that had run one command plus a read and a write, the same header read
 * "1 cmd" connected and "~3" disconnected, flipping on each reconnect.
 *
 * `isCommandTool` is the shared definition of the smaller set. It mirrors
 * `COMMAND_TOOL_NAMES` / `is_command_line` in the backend; the two are pinned
 * against each other by `backend/tests/test_frontend_contract.py`.
 */
describe("isCommandTool", () => {
  it("counts executed commands", () => {
    for (const name of ["bash", "execute_command", "shell_session", "shell_write"]) {
      expect(isCommandTool(name)).toBe(true);
    }
  });

  it("does not count file work", () => {
    for (const name of ["read_file", "write_file", "str_replace", "ls", "glob", "grep"]) {
      expect(isCommandTool(name)).toBe(false);
    }
  });

  it("does not count shell session bookkeeping", () => {
    for (const name of ["shell_view", "shell_wait", "shell_kill"]) {
      expect(isCommandTool(name)).toBe(false);
    }
  });

  it("is a strict subset of the terminal surface", () => {
    // Terminal still *renders* file work; it just doesn't call it a command.
    for (const name of ["bash", "shell_session", "shell_write", "execute_command"]) {
      expect(isTerminalTool(name)).toBe(true);
    }
    expect(isTerminalTool("read_file")).toBe(true);
    expect(isCommandTool("read_file")).toBe(false);
  });

  it("survives a non-string type without throwing", () => {
    // The panel's error boundary takes out the whole workspace subtree, so a
    // malformed event must not throw here (same reason isTerminalTool guards).
    expect(isCommandTool(undefined as unknown as string)).toBe(false);
    expect(isCommandTool(null as unknown as string)).toBe(false);
  });

  it("counts the divergence case from the live thread", () => {
    // thread 29eb16a7: one bash command, one read_file, one write_file.
    const events = ["bash", "read_file", "write_file"];
    expect(events.filter(isCommandTool)).toHaveLength(1);
    expect(events.filter(isTerminalTool)).toHaveLength(3);
  });
});
