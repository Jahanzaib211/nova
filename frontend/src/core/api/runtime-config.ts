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

export function useRuntimeConfig() {
  return useQuery<RuntimeConfig>({
    queryKey: ["runtime-config"],
    queryFn: async () => {
      const res = await fetchWithAuth("/api/runtime/config");
      if (!res.ok) {
        throw new Error(`Failed to fetch runtime config: ${res.status}`);
      }
      return res.json();
    },
    staleTime: 30_000,
  });
}
