import { describe, expect, it } from "vitest";

/**
 * Stream event parser tests for the v6 llm_error event.
 *
 * The backend's LLMErrorHandlingMiddleware emits a structured
 * llm_error event whenever the synthetic error fallback fires.
 * The frontend's hooks.ts parses this event and invokes the
 * onLlmError listener. These tests assert the parser's contract.
 *
 * The full hooks.ts module pulls in heavy LangChain / langgraph
 * dependencies that are heavy to load in a unit test. We test the
 * parser logic by extracting it into a small, pure function that
 * mirrors the production code path. This keeps the tests fast and
 * hermetic.
 */

interface ParsedLlmError {
  error_type: string;
  reason: string;
  detail: string;
  http_status: number | null;
  code: string | null;
}

// Mirror of the production parser in hooks.ts. Keep these in sync.
function parseLlmErrorEvent(event: unknown): ParsedLlmError | null {
  if (
    typeof event !== "object" ||
    event === null ||
    !("type" in event) ||
    (event as { type: unknown }).type !== "llm_error"
  ) {
    return null;
  }
  const e = event as {
    error_type?: unknown;
    reason?: unknown;
    detail?: unknown;
    http_status?: unknown;
    code?: unknown;
  };
  return {
    error_type: typeof e.error_type === "string" ? e.error_type : "Unknown",
    reason: typeof e.reason === "string" ? e.reason : "unknown",
    detail: typeof e.detail === "string" ? e.detail : "",
    http_status: typeof e.http_status === "number" ? e.http_status : null,
    code: typeof e.code === "string" ? e.code : null,
  };
}

describe("parseLlmErrorEvent", () => {
  it("returns parsed event for valid llm_error payload", () => {
    const event = {
      type: "llm_error",
      error_type: "QuotaExceeded",
      reason: "quota",
      detail: "Account balance is empty",
      http_status: 429,
      code: "insufficient_quota",
    };
    const result = parseLlmErrorEvent(event);
    expect(result).toEqual({
      error_type: "QuotaExceeded",
      reason: "quota",
      detail: "Account balance is empty",
      http_status: 429,
      code: "insufficient_quota",
    });
  });

  it("returns parsed event with null http_status/code when omitted", () => {
    const event = {
      type: "llm_error",
      error_type: "APIError",
      reason: "transient",
      detail: "Provider timeout",
    };
    const result = parseLlmErrorEvent(event);
    expect(result).toEqual({
      error_type: "APIError",
      reason: "transient",
      detail: "Provider timeout",
      http_status: null,
      code: null,
    });
  });

  it("returns null for non-llm_error events", () => {
    expect(parseLlmErrorEvent({ type: "task_progress", step: 1 })).toBeNull();
    expect(parseLlmErrorEvent({ type: "verify_result", ok: true })).toBeNull();
    expect(parseLlmErrorEvent({ type: "llm_retry", attempt: 1 })).toBeNull();
  });

  it("returns null for non-object / null inputs", () => {
    expect(parseLlmErrorEvent(null)).toBeNull();
    expect(parseLlmErrorEvent("llm_error")).toBeNull();
    expect(parseLlmErrorEvent(42)).toBeNull();
    expect(parseLlmErrorEvent(undefined)).toBeNull();
  });

  it("returns null for object without type field", () => {
    expect(parseLlmErrorEvent({ error_type: "X" })).toBeNull();
    expect(parseLlmErrorEvent({})).toBeNull();
  });

  it("fills defaults when fields have wrong types (defensive)", () => {
    const event = {
      type: "llm_error",
      error_type: 123,           // wrong type
      reason: null,              // wrong type
      detail: ["list", "not", "string"],
      http_status: "429",        // string not number
      code: 500,                // number not string
    };
    const result = parseLlmErrorEvent(event);
    expect(result).toEqual({
      error_type: "Unknown",
      reason: "unknown",
      detail: "",
      http_status: null,
      code: null,
    });
  });

  it("preserves known error reasons as strings (even if backend adds new ones)", () => {
    for (const reason of ["quota", "auth", "busy", "transient", "circuit_open", "future_reason"]) {
      const event = { type: "llm_error", error_type: "X", reason, detail: "" };
      const result = parseLlmErrorEvent(event);
      expect(result?.reason).toBe(reason);
    }
  });
});
