import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import {
  fetchIGINOStatus,
  toggleIGINO,
  runIGINOResearch,
  fetchIGINOCacheStats,
} from "./api";
import type { IGINOResearchResult } from "./types";

export function useIGINOStatus() {
  return useQuery({
    queryKey: ["igino", "status"],
    queryFn: fetchIGINOStatus,
    refetchInterval: 15000,
    retry: 1,
    staleTime: 10000,
  });
}

export function useToggleIGINO() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (enabled: boolean) => toggleIGINO(enabled),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["igino", "status"] });
      qc.invalidateQueries({ queryKey: ["runtime", "capabilities"] });
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
