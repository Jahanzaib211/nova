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

export function useCapabilities(threadId?: string) {
  const { data, isLoading, error, refetch, isFetching } =
    useQuery<CapabilitiesResponse>({
      // threadId is part of the key: the circuit snapshot is scoped to it, so
      // two chats must not share one cached answer.
      queryKey: ["runtime", "capabilities", threadId ?? null],
      queryFn: () => loadCapabilities(threadId),
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

/** Convenience hook — number of OPEN circuits. Returns 0 on error/loading.
 *
 * Pass ``threadId`` to count only that chat's breaker: the runtime bar is
 * per-chat, and a breaker opened elsewhere used to paint every chat degraded.
 */
export function useOpenCircuitCount(threadId?: string): number {
  const { capabilities } = useCapabilities(threadId);
  if (!capabilities) return 0;
  // `?? []`, because loadCapabilities bare-casts the response and every list
  // consumer in runtime-capabilities-bar already guards the same way. This hook
  // runs in that component's prologue -- before its own `!capabilities` bailout
  // -- and the bar mounts ABOVE ErrorBoundary scope="chat-main", so a missing
  // field here blanked the entire workspace rather than one panel.
  return (capabilities.circuits ?? []).filter((c) => c.state === "open").length;
}
