/**
 * Phase C0.3 tests — frontend correlation_id capture from SSE.
 *
 * The backend emits the correlation_id as an SSE comment frame:
 *   ``: correlation_id=<hex>\n\n``
 * This is invisible to the standard EventSource but our livenessFetch wrapper
 * tees the first chunk through a TextDecoder so we can capture the value.
 */

import { afterEach, beforeEach, describe, expect, it } from "vitest";

import {
  captureCorrelationIdFromChunk_FOR_TESTING,
  getCorrelationId,
  resetCorrelationIds,
} from "@/core/api/stream-liveness";

beforeEach(() => {
  resetCorrelationIds();
});

afterEach(() => {
  resetCorrelationIds();
});

describe("captureCorrelationIdFromChunk (Phase C0)", () => {
  it("captures a hex correlation_id from an SSE comment", () => {
    const decoder = new TextDecoder("utf-8");
    const chunk = new TextEncoder().encode(
      ": correlation_id=abc123def4567890\n\n",
    );
    captureCorrelationIdFromChunk_FOR_TESTING("thread-a", chunk, decoder);
    expect(getCorrelationId("thread-a")).toBe("abc123def4567890");
  });

  it("ignores comments that aren't correlation_id", () => {
    const decoder = new TextDecoder("utf-8");
    const chunk = new TextEncoder().encode(": heartbeat\n\n");
    captureCorrelationIdFromChunk_FOR_TESTING("thread-b", chunk, decoder);
    expect(getCorrelationId("thread-b")).toBeNull();
  });

  it("ignores empty / unknown threads", () => {
    const decoder = new TextDecoder("utf-8");
    const chunk = new TextEncoder().encode(
      ": correlation_id=some-hex-value\n\n",
    );
    captureCorrelationIdFromChunk_FOR_TESTING(null, chunk, decoder);
    expect(getCorrelationId(null)).toBeNull();
    expect(getCorrelationId("never-set")).toBeNull();
  });

  it("captures once and ignores subsequent duplicates", () => {
    const decoder = new TextDecoder("utf-8");
    captureCorrelationIdFromChunk_FOR_TESTING(
      "thread-c",
      new TextEncoder().encode(": correlation_id=first\n\n"),
      decoder,
    );
    captureCorrelationIdFromChunk_FOR_TESTING(
      "thread-c",
      new TextEncoder().encode(": correlation_id=second\n\n"),
      decoder,
    );
    expect(getCorrelationId("thread-c")).toBe("first");
  });

  it("accepts UUID hex with dashes (the standard UUID hex form)", () => {
    const decoder = new TextDecoder("utf-8");
    const chunk = new TextEncoder().encode(
      ": correlation_id=abc123de-f456-7890-abcd-1234567890ab\n\n",
    );
    captureCorrelationIdFromChunk_FOR_TESTING("thread-d", chunk, decoder);
    expect(getCorrelationId("thread-d")).toBe(
      "abc123de-f456-7890-abcd-1234567890ab",
    );
  });
});

describe("getCorrelationId (Phase C0)", () => {
  it("returns null for an unknown thread", () => {
    expect(getCorrelationId("nope")).toBeNull();
  });

  it("returns null for null/undefined", () => {
    expect(getCorrelationId(null)).toBeNull();
    expect(getCorrelationId(undefined)).toBeNull();
  });

  it("survives across multiple calls (registry is process-local)", () => {
    const decoder = new TextDecoder("utf-8");
    captureCorrelationIdFromChunk_FOR_TESTING(
      "thread-x",
      new TextEncoder().encode(": correlation_id=stable-id\n\n"),
      decoder,
    );
    expect(getCorrelationId("thread-x")).toBe("stable-id");
    expect(getCorrelationId("thread-x")).toBe("stable-id");
    expect(getCorrelationId("thread-x")).toBe("stable-id");
  });
});
