"use client";

import { useQuery } from "@tanstack/react-query";

import { fetchAgentsRegistry } from "./api";

export function useAgentsRegistry() {
  return useQuery({
    // The trailing "list" keeps this key off `["agents", <name>]`, the key
    // `useAgent` uses for a single custom agent: agent names match
    // /^[A-Za-z0-9-]+$/, so a custom agent literally named "registry" would
    // otherwise share this query's cache entry and serve the registry
    // payload as that agent.
    queryKey: ["agents", "registry", "list"],
    queryFn: fetchAgentsRegistry,
    // Live counts move while delegations run; otherwise there is nothing to poll.
    refetchInterval: (q) =>
      q.state.data?.agents.some((a) => a.running > 0 || a.queued > 0)
        ? 5_000
        : 60_000,
  });
}
