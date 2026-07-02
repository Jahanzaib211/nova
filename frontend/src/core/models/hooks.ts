import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import {
  createModel,
  deleteModel,
  loadModels,
  testModel,
  updateModel,
} from "./api";
import type { ModelWriteRequest } from "./types";

export const modelsQueryKey = ["models"] as const;

export function useModels({ enabled = true }: { enabled?: boolean } = {}) {
  const { data, isLoading, error } = useQuery({
    queryKey: modelsQueryKey,
    queryFn: () => loadModels(),
    enabled,
    refetchOnWindowFocus: false,
  });
  return {
    models: data?.models ?? [],
    tokenUsageEnabled: data?.token_usage?.enabled ?? false,
    isLoading,
    error,
  };
}

function useInvalidateModels() {
  const queryClient = useQueryClient();
  return () => void queryClient.invalidateQueries({ queryKey: modelsQueryKey });
}

export function useCreateModel() {
  const invalidate = useInvalidateModels();
  return useMutation({
    mutationFn: (request: ModelWriteRequest) => createModel(request),
    onSuccess: invalidate,
  });
}

export function useUpdateModel() {
  const invalidate = useInvalidateModels();
  return useMutation({
    mutationFn: ({
      name,
      request,
    }: {
      name: string;
      request: ModelWriteRequest;
    }) => updateModel(name, request),
    onSuccess: invalidate,
  });
}

export function useDeleteModel() {
  const invalidate = useInvalidateModels();
  return useMutation({
    mutationFn: (name: string) => deleteModel(name),
    onSuccess: invalidate,
  });
}

export function useTestModel() {
  return useMutation({
    mutationFn: (name: string) => testModel(name),
  });
}
