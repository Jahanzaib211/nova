import { fetch } from "@/core/api/fetcher";

import type {
  IGINOStatus,
  IGINOResearchResult,
  IGINOCacheStats,
} from "./types";

const BASE = "/api/igino";

export class IGINORequestError extends Error {
  status: number;
  constructor(message: string, status: number) {
    super(message);
    this.name = "IGINORequestError";
    this.status = status;
  }
}

async function iginoFetch<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await fetch(`${BASE}${path}`, {
    headers: { "Content-Type": "application/json" },
    ...init,
  });
  if (!res.ok) {
    const body = await res.text().catch(() => "");
    throw new IGINORequestError(body || `HTTP ${res.status}`, res.status);
  }
  return res.json();
}

export async function fetchIGINOStatus(): Promise<IGINOStatus> {
  return iginoFetch<IGINOStatus>("/status");
}

export async function runIGINOResearch(params: {
  query: string;
  max_results?: number;
  fetch_depth?: number;
  privacy?: boolean;
  timeout_s?: number;
}): Promise<IGINOResearchResult> {
  return iginoFetch<IGINOResearchResult>("/research", {
    method: "POST",
    body: JSON.stringify(params),
  });
}

export async function fetchIGINOCacheStats(): Promise<IGINOCacheStats> {
  return iginoFetch<IGINOCacheStats>("/cache");
}

/** Run one capability's self-test. GET because it is read-only with a fixed
    target, so a button can call it without a CSRF round-trip. */
export async function testIGINOCapability(tool: string): Promise<{
  tool: string;
  ok: boolean;
  detail: string;
  duration_ms: number;
}> {
  return iginoFetch(`/test/${encodeURIComponent(tool)}`);
}
