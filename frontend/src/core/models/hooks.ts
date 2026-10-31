import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useCallback, useSyncExternalStore } from "react";

import {
  createModel,
  deleteModel,
  loadModels,
  testModel,
  updateModel,
} from "./api";
import type { Model, ModelWriteRequest } from "./types";
import {
  clearModelVisibility,
  getModelVisibilityServerSnapshot,
  getModelVisibilitySnapshot,
  isModelVisible,
  setModelVisible,
  setShowHiddenModels,
  subscribeModelVisibility,
} from "./visibility";

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

/**
 * The user's per-browser model visibility (see `core/models/visibility.ts`).
 * Config models cannot be written through the models API, so "show in chat" for
 * them is a local preference; runtime models keep their server-side `hidden`
 * flag and only need this to read it uniformly.
 */
export function useModelVisibility() {
  const state = useSyncExternalStore(
    subscribeModelVisibility,
    getModelVisibilitySnapshot,
    getModelVisibilityServerSnapshot,
  );

  const isVisible = useCallback(
    (model: Pick<Model, "name" | "hidden">) => isModelVisible(model, state),
    [state],
  );

  return {
    state,
    isVisible,
    setVisible: setModelVisible,
    resetVisibility: clearModelVisibility,
    showHidden: state.showHidden,
    setShowHidden: setShowHiddenModels,
  };
}
