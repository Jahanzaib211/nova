/**
 * Tests for iGIN0 type definitions (igino/types.ts).
 *
 * Validates that the interface shapes match expected structure.
 * These are primarily compile-time checks but we also runtime-verify
 * that mock objects conform to the interfaces.
 */
import { describe, expect, test } from "vitest";

import type {
  IGINOStatus,
  IGINOToggleResponse,
  IGINOResearchResult,
  IGINOResearchItem,
  IGINOResearchMetadata,
  IGINOAuditRecord,
  IGINOCapabilities,
} from "@/core/igino/types";

describe("iGIN0 types compile", () => {
  test("IGINOStatus conforms to shape", () => {
    const status: IGINOStatus = {
      enabled: true,
      tor_enabled: false,
      tor_available: true,
      searxng_healthy: true,
      base_url: "http://localhost:8080",
      cache: {
        size: 10,
        max_size: 100,
        hits: 5,
        misses: 3,
        hit_rate: 0.625,
        ttl_s: 3600,
      },
      audit: {
        total_records: 50,
        errors: 2,
        tor_usage: 5,
        enabled: true,
        redacted: false,
      },
    };
    expect(status.enabled).toBe(true);
    expect(status.cache.hit_rate).toBe(0.625);
    expect(status.audit.tor_usage).toBe(5);
  });

  test("IGINOStatus optional error field", () => {
    const status: IGINOStatus = {
      enabled: false,
      tor_enabled: false,
      tor_available: false,
      searxng_healthy: false,
      base_url: "",
      cache: {
        size: 0,
        max_size: 0,
        hits: 0,
        misses: 0,
        hit_rate: 0,
        ttl_s: 0,
      },
      audit: {
        total_records: 0,
        errors: 0,
        tor_usage: 0,
        enabled: false,
        redacted: false,
      },
      error: "SearXNG unreachable",
    };
    expect(status.error).toBe("SearXNG unreachable");
  });

  test("IGINOToggleResponse conforms to shape", () => {
    const resp: IGINOToggleResponse = {
      enabled: true,
      message: "Toggle successful",
    };
    expect(resp.enabled).toBe(true);
  });

  test("IGINOResearchResult conforms to shape", () => {
    const result: IGINOResearchResult = {
      query: "test query",
      results: [
        {
          title: "Result 1",
          url: "https://example.com",
          snippet: "A snippet",
          content: "Full content",
          source: "searxng",
          score: 0.95,
        },
      ],
      metadata: {
        sources_searched: ["searxng"],
        sources_fetched: ["searxng"],
        sources_succeeded: 1,
        privacy_mode: true,
        tor_used: false,
        duration_ms: 150,
        cache_hit: false,
        errors: null,
      },
    };
    expect(result.results).toHaveLength(1);
    expect(result.metadata.privacy_mode).toBe(true);
    expect(result.metadata.duration_ms).toBe(150);
  });

  test("IGINOResearchItem null content", () => {
    const item: IGINOResearchItem = {
      title: "No content",
      url: "https://example.com",
      snippet: "snippet",
      content: null,
      source: "searxng",
      score: 0.5,
    };
    expect(item.content).toBeNull();
  });

  test("IGINOResearchMetadata nullable errors", () => {
    const meta: IGINOResearchMetadata = {
      sources_searched: [],
      sources_fetched: [],
      sources_succeeded: 0,
      privacy_mode: false,
      tor_used: false,
      duration_ms: 0,
      cache_hit: false,
      errors: ["timeout", "connection refused"],
    };
    expect(meta.errors).toEqual(["timeout", "connection refused"]);
  });

  test("IGINOAuditRecord conforms to shape", () => {
    const record: IGINOAuditRecord = {
      audit_id: "aud_123",
      timestamp: "2026-06-26T12:00:00Z",
      thread_id: "thread_abc",
      query: "test",
      privacy_mode: true,
      tor_used: false,
      sources_searched: ["searxng"],
      sources_fetched: ["searxng"],
      results_returned: 5,
      results_succeeded: 5,
      duration_ms: 200,
      cache_hit: true,
      error: null,
      compliance_tags: ["no-pii"],
    };
    expect(record.audit_id).toBe("aud_123");
    expect(record.compliance_tags).toContain("no-pii");
  });

  test("IGINOCapabilities conforms to shape", () => {
    const caps: IGINOCapabilities = {
      enabled: true,
      tor_enabled: false,
      tor_available: false,
      searxng_healthy: true,
      circuit_states: { default: "closed" },
      cache_stats: {
        size: 0,
        max_size: 100,
        hits: 0,
        misses: 0,
        hit_rate: 0,
        ttl_s: 3600,
      },
      audit_stats: {
        total_records: 0,
        errors: 0,
        tor_usage: 0,
        enabled: true,
        redacted: false,
      },
    };
    expect(caps.enabled).toBe(true);
    expect(caps.circuit_states.default).toBe("closed");
  });
});
