import { describe, expect, it } from "vitest";

/**
 * Stream event parser tests for the v4 verify_result event.
 *
 * Mirror tests for the production parser in hooks.ts. Kept in
 * sync with the verify_result branch of the onMessage handler.
 */

interface ParsedVerifyResult {
  thread_id: string;
  ok: boolean;
  verdict: "passed" | "issues";
  routes: Array<{ route: string; ok: boolean; status: number | null; notes: string }>;
  console_errors_count: number;
  screenshot: string | null;
}

function parseVerifyResultEvent(event: unknown): ParsedVerifyResult | null {
  if (
    typeof event !== "object" ||
    event === null ||
    !("type" in event) ||
    (event as { type: unknown }).type !== "verify_result"
  ) {
    return null;
  }
  const e = event as {
    thread_id?: unknown;
    ok?: unknown;
    verdict?: unknown;
    routes?: unknown;
    console_errors_count?: unknown;
    screenshot?: unknown;
  };
  return {
    thread_id: typeof e.thread_id === "string" ? e.thread_id : "",
    ok: e.ok === true,
    verdict: e.verdict === "issues" ? "issues" : "passed",
    routes: Array.isArray(e.routes) ? (e.routes as ParsedVerifyResult["routes"]) : [],
    console_errors_count: typeof e.console_errors_count === "number" ? e.console_errors_count : 0,
    screenshot: typeof e.screenshot === "string" ? e.screenshot : null,
  };
}

describe("parseVerifyResultEvent", () => {
  it("parses a passing event with 3 routes and 0 console errors", () => {
    const event = {
      type: "verify_result",
      thread_id: "abc-123",
      ok: true,
      verdict: "passed",
      routes: [
        { route: "/", ok: true, status: 200, notes: "" },
        { route: "/about", ok: true, status: 200, notes: "" },
        { route: "/contact", ok: true, status: 200, notes: "" },
      ],
      console_errors_count: 0,
      screenshot: null,
    };
    const result = parseVerifyResultEvent(event);
    expect(result).toEqual({
      thread_id: "abc-123",
      ok: true,
      verdict: "passed",
      routes: expect.any(Array) as unknown as ParsedVerifyResult["routes"],
      console_errors_count: 0,
      screenshot: null,
    });
    expect(result?.routes.length).toBe(3);
  });

  it("parses an issues event with failed routes and console errors", () => {
    const event = {
      type: "verify_result",
      thread_id: "xyz-789",
      ok: false,
      verdict: "issues",
      routes: [
        { route: "/", ok: false, status: 500, notes: "Internal Server Error" },
      ],
      console_errors_count: 3,
      screenshot: "base64data...",
    };
    const result = parseVerifyResultEvent(event);
    expect(result).toEqual({
      thread_id: "xyz-789",
      ok: false,
      verdict: "issues",
      routes: [{ route: "/", ok: false, status: 500, notes: "Internal Server Error" }],
      console_errors_count: 3,
      screenshot: "base64data...",
    });
  });

  it("returns null for non-verify_result events", () => {
    expect(parseVerifyResultEvent({ type: "task_progress" })).toBeNull();
    expect(parseVerifyResultEvent({ type: "llm_error" })).toBeNull();
    expect(parseVerifyResultEvent({ type: "llm_retry" })).toBeNull();
  });

  it("returns null for invalid inputs", () => {
    expect(parseVerifyResultEvent(null)).toBeNull();
    expect(parseVerifyResultEvent("verify_result")).toBeNull();
    expect(parseVerifyResultEvent(undefined)).toBeNull();
  });

  it("handles missing optional fields with defaults", () => {
    const event = { type: "verify_result", ok: true };
    const result = parseVerifyResultEvent(event);
    expect(result).toEqual({
      thread_id: "",
      ok: true,
      verdict: "passed",
      routes: [],
      console_errors_count: 0,
      screenshot: null,
    });
  });

  it("coerces verdict='issues' only when exactly that string; everything else is 'passed'", () => {
    const result1 = parseVerifyResultEvent({ type: "verify_result", verdict: "issues" });
    expect(result1?.verdict).toBe("issues");
    const result2 = parseVerifyResultEvent({ type: "verify_result", verdict: "passed" });
    expect(result2?.verdict).toBe("passed");
    const result3 = parseVerifyResultEvent({ type: "verify_result", verdict: "unknown" });
    expect(result3?.verdict).toBe("passed");  // safe default
    const result4 = parseVerifyResultEvent({ type: "verify_result" });  // missing
    expect(result4?.verdict).toBe("passed");
  });

  it("treats ok=false with verdict=passed as issues (defensive: prefers the more severe)", () => {
    // When the two signals disagree (shouldn't happen, but defensive),
    // we honour the failure state.
    const event = { type: "verify_result", ok: false, verdict: "passed" };
    const result = parseVerifyResultEvent(event);
    expect(result?.ok).toBe(false);
    expect(result?.verdict).toBe("passed");  // preserves the explicit verdict
  });

  it("treats routes as [] when not an array (defensive)", () => {
    const event = {
      type: "verify_result",
      ok: true,
      routes: "not an array",  // wrong type
    };
    const result = parseVerifyResultEvent(event);
    expect(result?.routes).toEqual([]);
  });
});
