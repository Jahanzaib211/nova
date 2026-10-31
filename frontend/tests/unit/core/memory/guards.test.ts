/**
 * `GET /api/memory` used to be cast straight to `UserMemory`; a payload
 * missing `user.workContext` then threw inside render and took the whole
 * settings route down ("Something went wrong"). The guard now runs on every
 * memory response so malformed data becomes a handled error instead.
 */
import { describe, expect, it } from "vitest";

import { readMemoryResponse } from "@/core/memory/api";
import { isUserMemory, MalformedMemoryError } from "@/core/memory/guards";

const section = { summary: "", updatedAt: "2026-09-18T00:00:00Z" };
const VALID = {
  version: "1",
  lastUpdated: "2026-09-18T00:00:00Z",
  user: { workContext: section, personalContext: section, topOfMind: section },
  history: {
    recentMonths: section,
    earlierContext: section,
    longTermBackground: section,
  },
  facts: [
    {
      id: "f1",
      content: "likes tea",
      category: "preference",
      confidence: 0.9,
      createdAt: "2026-09-18T00:00:00Z",
      source: "chat",
    },
  ],
};

describe("isUserMemory", () => {
  it("accepts a well-formed document", () => {
    expect(isUserMemory(VALID)).toBe(true);
  });

  it("rejects the shapes that used to crash the page", () => {
    expect(isUserMemory({ enabled: false, memories: [] })).toBe(false);
    expect(isUserMemory({ ...VALID, user: {} })).toBe(false);
    expect(isUserMemory({ ...VALID, facts: [{ id: 1 }] })).toBe(false);
    expect(isUserMemory(null)).toBe(false);
    expect(isUserMemory("nope")).toBe(false);
  });
});

describe("readMemoryResponse", () => {
  const response = (body: unknown, ok = true) =>
    ({
      ok,
      status: ok ? 200 : 500,
      statusText: ok ? "OK" : "Internal Server Error",
      json: async () => body,
    }) as unknown as Response;

  it("returns the document when it validates", async () => {
    await expect(readMemoryResponse(response(VALID), "x")).resolves.toEqual(
      VALID,
    );
  });

  it("throws MalformedMemoryError instead of returning a partial object", async () => {
    await expect(
      readMemoryResponse(response({ enabled: false }), "x"),
    ).rejects.toBeInstanceOf(MalformedMemoryError);
  });

  it("still surfaces server errors with their detail", async () => {
    await expect(
      readMemoryResponse(response({ detail: "boom" }, false), "Failed"),
    ).rejects.toThrow("boom");
  });
});
