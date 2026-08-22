import { execSync } from "node:child_process";
import path from "node:path";

import { describe, expect, it } from "vitest";

/**
 * Every custom stream event the backend emits must have a frontend consumer.
 *
 * This exists because the gap was invisible rather than missed. The backend
 * emitted six `task_*` events; the frontend handled one; and the e2e suite
 * could not have caught it, because the mock had no way to emit a `custom`
 * frame at all. The result was that a subagent which had completed showed
 * "Subtask failed" -- the UI fell back to guessing a status the backend was
 * already broadcasting.
 *
 * A grep is the right shape of check here: the producer names these events as
 * string literals and the consumer matches them as string literals, so there is
 * no type that could relate the two.
 */
const REPO_ROOT = path.resolve(__dirname, "../../..");
const BACKEND = path.join(REPO_ROOT, "backend/packages/harness/deerflow");
const FRONTEND_SRC = path.join(REPO_ROOT, "frontend/src");

function emittedTaskEvents(): string[] {
  const out = execSync(
    `grep -rhoE '"type": "task_[a-z_]+"' ${JSON.stringify(BACKEND)} || true`,
    { encoding: "utf8" },
  );
  const names = out
    .split("\n")
    .map((line) => /"type": "(task_[a-z_]+)"/.exec(line)?.[1])
    .filter((n): n is string => Boolean(n));
  return [...new Set(names)].sort();
}

function isHandledInFrontend(event: string): boolean {
  const out = execSync(
    `grep -rl ${JSON.stringify(`"${event}"`)} ${JSON.stringify(FRONTEND_SRC)} || true`,
    { encoding: "utf8" },
  );
  return out.trim().length > 0;
}

describe("backend task_* stream events", () => {
  it("emits at least the known lifecycle set", () => {
    // Guards the guard: if the grep stops matching, the test below would pass
    // vacuously and we would be back to shipping unconsumed events.
    expect(emittedTaskEvents().length).toBeGreaterThanOrEqual(5);
  });

  it("are all consumed by the frontend", () => {
    const unhandled = emittedTaskEvents().filter((e) => !isHandledInFrontend(e));
    expect(
      unhandled,
      `Backend emits these with no frontend consumer: ${unhandled.join(", ")}. ` +
        `Add a handler in src/core/threads/hooks.ts onCustomEvent, or the UI will ` +
        `fall back to guessing the state instead of being told it.`,
    ).toEqual([]);
  });
});
