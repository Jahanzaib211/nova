import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";

import {
  fetchIGINOStatus,
  toggleIGINO,
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

export function useToggleIGINO() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (enabled: boolean) => toggleIGINO(enabled),
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: ["igino", "status"] });
      void qc.invalidateQueries({ queryKey: ["runtime", "capabilities"] });
    },
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
