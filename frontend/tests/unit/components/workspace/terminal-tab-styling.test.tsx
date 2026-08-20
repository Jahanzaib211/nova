import { describe, expect, test } from "vitest";

import {
  TERMINAL_TOOLS,
  terminalOutputClass,
} from "@/components/workspace/agent-computer/terminal-tab";

describe("TERMINAL_TOOLS", () => {
  test("includes bash, execute_command, search_files, grep_files", () => {
    for (const t of ["bash", "execute_command", "search_files", "grep_files"]) {
      expect(TERMINAL_TOOLS.has(t)).toBe(true);
    }
  });

  test("includes read_file, write_file, str_replace (2026-08-14 fix)", () => {
    for (const t of ["read_file", "write_file", "str_replace"]) {
      expect(TERMINAL_TOOLS.has(t)).toBe(true);
    }
  });
});

describe("terminalOutputClass", () => {
  test("a 'done' tool whose output starts with 'Error:' still renders emerald", () => {
    // Regression for the 2026-08-14 false-positive bug: 40+ tools legitimately
    // return ``f"Error: …"`` on success paths. The Terminal-tab output block
    // must not color that red just because the text starts with "Error:".
    const cls = terminalOutputClass("done");
    expect(cls).toContain("border-emerald-900/30");
    expect(cls).not.toContain("border-red-900/50");
  });

  test("an actual error tool still renders red", () => {
    const cls = terminalOutputClass("error");
    expect(cls).toContain("border-red-900/50");
    expect(cls).not.toContain("border-emerald-900/30");
  });
});
