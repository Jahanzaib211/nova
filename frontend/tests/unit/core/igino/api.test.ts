/**
 * Tests for iGIN0 API client (igino/api.ts).
 *
 * Covers:
 *   - fetchIGINOStatus returns status on 200
 *   - toggleIGINO is gone: the endpoint never wrote any state, so the
 *     switch it backed did nothing while appearing to work. Capabilities
 *     are configured per-feature by environment and reported by /status.
 *   - runIGINOResearch sends query params
 *   - fetchIGINOCacheStats returns cache stats
 *   - IGINORequestError thrown on non-OK responses
 *   - Empty body fallback when res.text() fails
 */
import { beforeEach, describe, expect, test, vi } from "vitest";

vi.mock("@/core/api/fetcher", () => ({
  fetch: vi.fn(),
}));

import { fetch as fetcher } from "@/core/api/fetcher";
import {
  fetchIGINOStatus,
  runIGINOResearch,
  fetchIGINOCacheStats,
  IGINORequestError,
} from "@/core/igino/api";

const mockedFetch = vi.mocked(fetcher);

function jsonResponse(status: number, body: unknown): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

function textResponse(status: number, text: string): Response {
  return new Response(text, { status });
}

beforeEach(() => {
  vi.clearAllMocks();
});

describe("fetchIGINOStatus", () => {
  test("returns status on 200", async () => {
    const payload = {
      enabled: true,
      searxng_healthy: true,
      tor_available: false,
      cache: { size: 10, max_size: 100, hit_rate: 0.5, ttl_s: 3600 },
      audit: { total_records: 50, errors: 2, tor_usage: 5 },
    };
    mockedFetch.mockResolvedValue(jsonResponse(200, payload));

    const result = await fetchIGINOStatus();

    expect(result).toEqual(payload);
    expect(mockedFetch).toHaveBeenCalledWith(
      "/api/igino/status",
      expect.objectContaining({
        headers: { "Content-Type": "application/json" },
      }),
    );
  });

  test("throws IGINORequestError on 500", async () => {
    mockedFetch.mockResolvedValue(textResponse(500, "Internal Server Error"));

    await expect(fetchIGINOStatus()).rejects.toThrow(IGINORequestError);
    await expect(fetchIGINOStatus()).rejects.toMatchObject({ status: 500 });
  });
});

describe("runIGINOResearch", () => {
  test("sends query params via POST body", async () => {
    const payload = { query: "test", results: [], cached: false };
    mockedFetch.mockResolvedValue(jsonResponse(200, payload));

    const result = await runIGINOResearch({
      query: "test",
      max_results: 10,
      privacy: true,
    });

    expect(result).toEqual(payload);
    expect(mockedFetch).toHaveBeenCalledWith(
      "/api/igino/research",
      expect.objectContaining({
        method: "POST",
        body: JSON.stringify({ query: "test", max_results: 10, privacy: true }),
      }),
    );
  });
});

describe("fetchIGINOCacheStats", () => {
  test("returns cache stats on 200", async () => {
    const payload = {
      size: 25,
      max_size: 200,
      hit_rate: 0.8,
      ttl_s: 7200,
      entries: [],
    };
    mockedFetch.mockResolvedValue(jsonResponse(200, payload));

    const result = await fetchIGINOCacheStats();

    expect(result).toEqual(payload);
    expect(mockedFetch).toHaveBeenCalledWith(
      "/api/igino/cache",
      expect.objectContaining({
        headers: { "Content-Type": "application/json" },
      }),
    );
  });
});

describe("IGINORequestError", () => {
  test("has correct name and status", () => {
    const err = new IGINORequestError("test error", 422);
    expect(err.name).toBe("IGINORequestError");
    expect(err.status).toBe(422);
    expect(err.message).toBe("test error");
    expect(err).toBeInstanceOf(Error);
  });
});

describe("iginoFetch error handling", () => {
  test("uses HTTP status text when body is empty", async () => {
    mockedFetch.mockResolvedValue(new Response(null, { status: 404 }));

    await expect(fetchIGINOStatus()).rejects.toThrow("HTTP 404");
  });

  test("uses response body text when available", async () => {
    mockedFetch.mockResolvedValue(textResponse(400, "Bad request details"));

    await expect(fetchIGINOStatus()).rejects.toThrow("Bad request details");
  });
});
