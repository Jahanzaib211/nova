"use client";

import { useQuery } from "@tanstack/react-query";

import { fetch as fetchWithAuth } from "@/core/api/fetcher";

export type RuntimeConfig = {
  summarization: {
    enabled: boolean;
    model_name: string | null;
    trigger_type: string | null;
    trigger_value: number | null;
    keep_type: string | null;
    keep_value: number | null;
  };
  subagents: {
    default_timeout_seconds: number | null;
    max_turns: number | null;
    custom_agents_count: number;
  };
  guardrails: {
    enabled: boolean;
    fail_closed: boolean;
    passport: string | null;
    provider_class: string | null;
  };
};

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null;
}

const str = (v: unknown): string | null => (typeof v === "string" ? v : null);
const num = (v: unknown): number | null =>
  typeof v === "number" && Number.isFinite(v) ? v : null;
const bool = (v: unknown): boolean => v === true;

/**
 * Coerce whatever the gateway sent into the full `RuntimeConfig` shape.
 * Missing or mistyped fields become null/false/0 rather than `undefined`,
 * which the page would otherwise print literally ("undefined: ?").
 */
export function normalizeRuntimeConfig(payload: unknown): RuntimeConfig {
  const root = isRecord(payload) ? payload : {};
  const summarization = isRecord(root.summarization) ? root.summarization : {};
  const subagents = isRecord(root.subagents) ? root.subagents : {};
  const guardrails = isRecord(root.guardrails) ? root.guardrails : {};
  return {
    summarization: {
      enabled: bool(summarization.enabled),
      model_name: str(summarization.model_name),
      trigger_type: str(summarization.trigger_type),
      trigger_value: num(summarization.trigger_value),
      keep_type: str(summarization.keep_type),
      keep_value: num(summarization.keep_value),
    },
    subagents: {
      default_timeout_seconds: num(subagents.default_timeout_seconds),
      max_turns: num(subagents.max_turns),
      custom_agents_count: num(subagents.custom_agents_count) ?? 0,
    },
    guardrails: {
      enabled: bool(guardrails.enabled),
      fail_closed: bool(guardrails.fail_closed),
      passport: str(guardrails.passport),
      provider_class: str(guardrails.provider_class),
    },
  };
}

export function useRuntimeConfig() {
  return useQuery<RuntimeConfig>({
    queryKey: ["runtime-config"],
    queryFn: async () => {
      const res = await fetchWithAuth("/api/runtime/config");
      if (!res.ok) {
        throw new Error(`Failed to fetch runtime config: ${res.status}`);
      }
      return normalizeRuntimeConfig(await res.json());
    },
    staleTime: 30_000,
  });
}
