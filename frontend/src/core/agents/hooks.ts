import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import {
  AgentsApiDisabledError,
  createAgent,
  deleteAgent,
  getAgent,
  listAgents,
  updateAgent,
} from "./api";
import type { CreateAgentRequest, UpdateAgentRequest } from "./types";

export function useAgents() {
  const { data, isLoading, error } = useQuery({
    queryKey: ["agents"],
    queryFn: () => listAgents(),
    // A disabled agents API is a deterministic config state, not a transient
    // failure — never retry it (avoids a multi-second phantom "Loading…").
    // Other failures (network, 5xx) get a bounded retry.
    retry: (failureCount, err) =>
      !(err instanceof AgentsApiDisabledError) && failureCount < 2,
  });
  return {
    agents: data ?? [],
    isLoading,
    error,
    isDisabled: error instanceof AgentsApiDisabledError,
  };
}

export function useAgent(name: string | null | undefined) {
  const { data, isLoading, error } = useQuery({
    queryKey: ["agents", name],
    queryFn: () => getAgent(name!),
    enabled: !!name,
  });
  return { agent: data ?? null, isLoading, error };
}

export function useCreateAgent() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (request: CreateAgentRequest) => createAgent(request),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ["agents"] });
    },
  });
}

export function useUpdateAgent() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: ({
      name,
      request,
    }: {
      name: string;
      request: UpdateAgentRequest;
    }) => updateAgent(name, request),
    onSuccess: (_data, { name }) => {
      void queryClient.invalidateQueries({ queryKey: ["agents"] });
      void queryClient.invalidateQueries({ queryKey: ["agents", name] });
    },
  });
}

export function useDeleteAgent() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (name: string) => deleteAgent(name),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ["agents"] });
    },
  });
}
