import { useQuery, useMutation } from "@tanstack/react-query";

import {
  fetchIGINOStatus,
  testIGINOCapability,
  runIGINOResearch,
  fetchIGINOCacheStats,
} from "./api";

export function useIGINOStatus(enabled = true) {
  return useQuery({
    queryKey: ["igino", "status"],
    queryFn: fetchIGINOStatus,
    enabled,
    refetchInterval: enabled ? 15000 : false,
    retry: 1,
    staleTime: 10000,
  });
}

export function useIGINOResearch() {
  return useMutation({
    mutationFn: (params: {
      query: string;
      max_results?: number;
      fetch_depth?: number;
      privacy?: boolean;
      timeout_s?: number;
    }) => runIGINOResearch(params),
  });
}

export function useIGINOCacheStats() {
  return useQuery({
    queryKey: ["igino", "cache"],
    queryFn: fetchIGINOCacheStats,
    refetchInterval: 30000,
    retry: 1,
  });
}

/** Self-test one capability. Not a query: it must only run when clicked, since
    it performs a real search or fetch rather than reading cached state. */
export function useTestIGINOCapability() {
  return useMutation({
    mutationFn: (tool: string) => testIGINOCapability(tool),
  });
}
