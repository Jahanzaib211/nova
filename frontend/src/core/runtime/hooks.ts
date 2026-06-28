/** React hooks for runtime capabilities polling.

 * - useCapabilities(): react-query hook, polls every 30s
 * - useOpenCircuitCount(): derived selector, returns number of OPEN circuits
 *
 * The 30s polling interval matches /api/health/browser cadence and avoids
 * over-fetching when the bar is mounted in multiple chat pages.
 */

import { useQuery } from "@tanstack/react-query";

import { loadCapabilities } from "./api";
import type { CapabilitiesResponse } from "./types";

const POLL_INTERVAL_MS = 30_000;

export function useCapabilities() {
  const { data, isLoading, error, refetch, isFetching } =
    useQuery<CapabilitiesResponse>({
      queryKey: ["runtime", "capabilities"],
      queryFn: () => loadCapabilities(),
      staleTime: POLL_INTERVAL_MS / 2,
      refetchInterval: POLL_INTERVAL_MS,
      refetchOnWindowFocus: true,
      retry: 1,
    });
  return {
    capabilities: data,
    isLoading,
    error,
    isFetching,
    refetch,
  };
}

/** Convenience hook — number of OPEN circuits. Returns 0 on error/loading. */
export function useOpenCircuitCount(): number {
  const { capabilities } = useCapabilities();
  if (!capabilities) return 0;
  return capabilities.circuits.filter((c) => c.state === "open").length;
}
