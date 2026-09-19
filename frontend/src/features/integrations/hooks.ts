"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import {
  type IntegrationsList,
  listIntegrations,
  probeIntegration,
} from "./api";
import type { IntegrationHealthItem } from "./types";

export const INTEGRATIONS_KEY = ["integrations"] as const;

export function useIntegrations() {
  return useQuery({
    queryKey: INTEGRATIONS_KEY,
    queryFn: () => listIntegrations(false),
    refetchInterval: 60_000,
  });
}

/** Re-probe everything (`?refresh=1`) or one card, updating the cached list in place. */
export function useProbeIntegrations() {
  const qc = useQueryClient();
  const all = useMutation({
    mutationFn: () => listIntegrations(true),
    onSuccess: (data) => qc.setQueryData(INTEGRATIONS_KEY, data),
  });
  const one = useMutation({
    mutationFn: (id: string) => probeIntegration(id),
    onSuccess: (item: IntegrationHealthItem) =>
      qc.setQueryData<IntegrationsList>(INTEGRATIONS_KEY, (prev) =>
        prev
          ? {
              ...prev,
              integrations: prev.integrations.map((i) =>
                i.id === item.id ? item : i,
              ),
            }
          : prev,
      ),
  });
  return { all, one };
}
