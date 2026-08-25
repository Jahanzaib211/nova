import { readFileSync } from "node:fs";

import { describe, expect, it } from "vitest";

// The task tool is bound by `subagent_enabled` in useThreadStream's run
// context. Coupling it to the UI mode silently removed subagents from every
// flash/thinking run - observed live as a 341K-token solo lead-agent call
// that told the user "Subagent tool isn't actually wired up".
describe("subagent binding", () => {
  it("is unconditional, not mode-coupled", () => {
    const src = readFileSync("src/core/threads/hooks.ts", "utf-8");
    expect(src).toMatch(/subagent_enabled:\s*true,/);
    expect(src).not.toMatch(
      /subagent_enabled:\s*\n?\s*context\.mode\s*===/,
    );
  });
});
