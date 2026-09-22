"use client";

import {
  useMutation,
  useQuery,
  useQueryClient,
  type UseQueryOptions,
} from "@tanstack/react-query";

import {
  type CapabilityOpName,
  type CapabilityOps,
  invoke,
  listOps,
} from "./api";

export const CAPABILITY_KEY = ["capabilities"] as const;

export function capabilityKey<N extends CapabilityOpName>(
  name: N,
  input: CapabilityOps[N]["input"],
) {
  return [...CAPABILITY_KEY, name, input] as const;
}

/** Read an operation as a query; the key is the op + its input. */
export function useCapability<N extends CapabilityOpName>(
  name: N,
  input: CapabilityOps[N]["input"],
  options?: Omit<
    UseQueryOptions<CapabilityOps[N]["output"]>,
    "queryKey" | "queryFn"
  >,
) {
  return useQuery<CapabilityOps[N]["output"]>({
    queryKey: capabilityKey(name, input),
    queryFn: () => invoke(name, input),
    ...options,
  });
}

/** Run a write/execute operation; invalidates the given ops on success. */
export function useCapabilityMutation<N extends CapabilityOpName>(
  name: N,
  invalidates: readonly CapabilityOpName[] = [],
) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (input: CapabilityOps[N]["input"]) => invoke(name, input),
    onSuccess: async () => {
      await Promise.all(
        invalidates.map((op) =>
          qc.invalidateQueries({ queryKey: [...CAPABILITY_KEY, op] }),
        ),
      );
    },
  });
}

/** The whole registry: snapshot, per-module status, feature flags. */
export function useCapabilityOps() {
  return useQuery({
    queryKey: [...CAPABILITY_KEY, "ops"],
    queryFn: listOps,
    refetchInterval: 60_000,
  });
}
