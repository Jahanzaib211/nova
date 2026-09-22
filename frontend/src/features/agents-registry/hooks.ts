"use client";

import { useQuery } from "@tanstack/react-query";

import { fetchAgentsRegistry } from "./api";

export function useAgentsRegistry() {
  return useQuery({
    queryKey: ["agents", "registry"],
    queryFn: fetchAgentsRegistry,
    // Live counts move while delegations run; otherwise there is nothing to poll.
    refetchInterval: (q) =>
      q.state.data?.agents.some((a) => a.running > 0 || a.queued > 0)
        ? 5_000
        : 60_000,
  });
}
