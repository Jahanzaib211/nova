"use client";

import { useMemo } from "react";

import { env } from "@/env";
import type { FeatureFlagKey } from "@/features/types";

import { useCapabilities } from "./hooks";
import type { ServerFeatureFlags } from "./types";

export type FeatureFlags = Record<FeatureFlagKey, boolean>;

const ALL_OFF: FeatureFlags = {
  jobs: false,
  integrations: false,
  email_marketing: false,
  acp_agents: false,
  capabilities: false,
  runtimes: false,
};

/**
 * Parse `NEXT_PUBLIC_NOVA_FEATURES` ("jobs,integrations") into flags. Used
 * for local UI work on a feature the gateway does not advertise yet; the
 * server-declared flags from /api/runtime/capabilities are merged in when a
 * feature ships its backend.
 */
export function parseFeatureFlags(raw: string | undefined): FeatureFlags {
  const on = new Set(
    (raw ?? "")
      .split(",")
      .map((s) => s.trim())
      .filter(Boolean),
  );
  const flags = { ...ALL_OFF };
  for (const key of Object.keys(flags) as FeatureFlagKey[]) {
    if (on.has(key) || on.has("all")) flags[key] = true;
  }
  return flags;
}

/** Server flags win when present; the env override can only add. */
export function mergeFeatureFlags(
  env_: FeatureFlags,
  server: ServerFeatureFlags | undefined,
): FeatureFlags {
  const merged = { ...env_ };
  for (const key of Object.keys(merged) as FeatureFlagKey[]) {
    if (server?.[key]) merged[key] = true;
  }
  return merged;
}

export function useFeatureFlags(): FeatureFlags {
  const { capabilities } = useCapabilities();
  const server = capabilities?.features;
  return useMemo(
    () =>
      mergeFeatureFlags(
        parseFeatureFlags(env.NEXT_PUBLIC_NOVA_FEATURES),
        server,
      ),
    [server],
  );
}
