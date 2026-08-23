import fs from "node:fs";
import path from "node:path";

import { describe, expect, it } from "vitest";

/**
 * Every `fetch(...)` inside a react-query `queryFn` must check `res.ok`
 * before parsing.
 *
 * FastAPI answers errors with `{"detail": "..."}`, which is a truthy object.
 * So an unchecked `res.json()` hands that body back as if it were data, every
 * `?? []` / `?? null` fallback written to catch a missing response fails to
 * fire, and the consumer dereferences a field that was never there. That is
 * what blanked the whole workspace via `capabilities.circuits`, showed a
 * blocking consent modal to users who had already accepted, and killed four
 * Agent's Computer tabs.
 *
 * Eight call sites were fixed. This exists because nothing stopped the ninth,
 * and the pattern has already recurred once: `useSandboxTodo` was revived from
 * dormancy without the guard its siblings had -- its own comment says so.
 *
 * A grep is the right shape here. The producer is a runtime HTTP response and
 * the consumer is a hand-written cast; no type relates them, so there is
 * nothing for the compiler to check.
 */
const CORE = path.resolve(__dirname, "../../src/core");

function walk(dir: string): string[] {
  return fs.readdirSync(dir, { withFileTypes: true }).flatMap((e) => {
    const p = path.join(dir, e.name);
    if (e.isDirectory()) return walk(p);
    return e.isFile() && /\.tsx?$/.test(e.name) ? [p] : [];
  });
}

/** `queryFn` bodies, paired with the file and line they start on. */
function queryFnBodies(src: string, file: string) {
  const out: { file: string; line: number; body: string }[] = [];
  const re = /queryFn\s*:/g;
  let m: RegExpExecArray | null;
  while ((m = re.exec(src)) !== null) {
    // Take a generous window; a queryFn longer than this is the real smell.
    const body = src.slice(m.index, m.index + 1200);
    out.push({
      file,
      line: src.slice(0, m.index).split("\n").length,
      body,
    });
  }
  return out;
}

function violations(): string[] {
  const bad: string[] = [];
  for (const file of walk(CORE)) {
    const src = fs.readFileSync(file, "utf8");
    for (const { line, body } of queryFnBodies(src, file)) {
      const fetches = body.includes("await fetch(") || body.includes("fetch(");
      const parses = body.includes(".json()");
      if (!fetches || !parses) continue;
      // The guard may be `if (!res.ok)`, `res.ok ?`, or a named response var.
      if (/\bif\s*\(\s*!\s*\w+\.ok\s*\)|\w+\.ok\s*\?|\bif\s*\(\s*\w+\.ok\s*\)/.test(body)) {
        continue;
      }
      bad.push(`${path.relative(CORE, file)}:${line}`);
    }
  }
  return bad.sort();
}

describe("queryFn fetch/parse contract", () => {
  it("finds queryFns to check (guards the guard)", () => {
    // If the scan stops matching, the assertion below would pass vacuously.
    const total = walk(CORE)
      .map((f) => queryFnBodies(fs.readFileSync(f, "utf8"), f).length)
      .reduce((a, b) => a + b, 0);
    expect(total).toBeGreaterThan(10);
  });

  it("never parses a response without checking res.ok", () => {
    const bad = violations();
    expect(
      bad,
      `These queryFns call .json() without checking res.ok first:\n  ${bad.join(
        "\n  ",
      )}\nAn error body is truthy, so it will be handed back as data and the ` +
        `caller will dereference a field that is not there.`,
    ).toEqual([]);
  });
});
