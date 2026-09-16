import { describe, expect, it } from "vitest";

import { EMPTY_TODO_RESULT, normalizeTodoResult } from "@/core/sandbox/hooks";

/**
 * The exact shape that blanked the workspace.
 *
 * `/api/sandbox/todo` 404s for any thread whose directory does not exist yet —
 * which is every brand-new chat, and precisely when this hook is enabled. The
 * old code parsed that response straight through, so `todos` came back
 * undefined and `WorkspaceStateProvider` threw on `.map`, escaping to the root
 * ErrorBoundary because it mounts above the panel's own boundary.
 *
 * `res.ok` is now checked before parsing, and this normaliser is the second
 * line of defence for anything that gets past it — a 200 with an unexpected
 * body, a proxy's HTML error page parsed as JSON, a future backend change.
 */
describe("normalizeTodoResult", () => {
  it("rejects FastAPI's error body instead of passing it through", () => {
    // The actual 404 payload. It is an object, so it is truthy — which is why
    // every `?? []` fallback downstream failed to fire.
    expect(normalizeTodoResult({ detail: "Not found" })).toBe(
      EMPTY_TODO_RESULT,
    );
  });

  it("rejects a body whose todos is not an array", () => {
    expect(normalizeTodoResult({ content: "x", todos: null })).toBe(
      EMPTY_TODO_RESULT,
    );
    expect(normalizeTodoResult({ content: "x", todos: "nope" })).toBe(
      EMPTY_TODO_RESULT,
    );
  });

  it("survives null, undefined and primitives", () => {
    for (const junk of [null, undefined, 0, "", "text", true]) {
      expect(normalizeTodoResult(junk)).toBe(EMPTY_TODO_RESULT);
    }
  });

  it("passes a well-formed payload through", () => {
    const good = {
      content: "# todo",
      todos: [{ description: "do it", status: "pending" as const }],
    };
    expect(normalizeTodoResult(good)).toEqual(good);
  });

  it("defaults a missing content to empty string rather than undefined", () => {
    // The consumer renders `content`; undefined would print "undefined".
    const r = normalizeTodoResult({ todos: [] });
    expect(r.content).toBe("");
    expect(r.todos).toEqual([]);
  });

  it("returns one shared reference so dependency arrays stay stable", () => {
    // useMemo deps compare by identity: a fresh object each poll would
    // recompute the merged todo list on every 2s tick.
    expect(normalizeTodoResult({ detail: "a" })).toBe(
      normalizeTodoResult({ detail: "b" }),
    );
  });
});
