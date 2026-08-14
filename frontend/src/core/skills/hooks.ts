import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import {
  deleteCustomSkill,
  loadCustomSkill,
  loadCustomSkillHistory,
  rollbackCustomSkill,
  updateCustomSkill,
} from "./api";
import { enableSkill } from "./api";

import { loadSkills } from ".";

export function useSkills() {
  const { data, isLoading, error } = useQuery({
    queryKey: ["skills"],
    queryFn: () => loadSkills(),
  });
  // `data` is now always an array (loadSkills returns [] on non-OK), but
  // we keep the defensive fallback for the brief window before the query
  // settles on its first successful response.
  return { skills: data ?? [], isLoading, error };
}

export function useEnableSkill() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: async ({
      skillName,
      enabled,
    }: {
      skillName: string;
      enabled: boolean;
    }) => {
      await enableSkill(skillName, enabled);
    },
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ["skills"] });
    },
    onError: () => {
      // The Switch is controlled by query data, not local state — on
      // failure there's nothing optimistic to roll back, but the query
      // still needs invalidating so a stale cached "enabled" value (if
      // any) doesn't linger. The caller surfaces the actual error message.
      void queryClient.invalidateQueries({ queryKey: ["skills"] });
    },
  });
}

export function useCustomSkill(skillName: string | null) {
  return useQuery({
    queryKey: ["skills", "custom", skillName],
    enabled: skillName !== null,
    queryFn: () => loadCustomSkill(skillName!),
  });
}

export function useCustomSkillHistory(skillName: string | null) {
  return useQuery({
    queryKey: ["skills", "custom", skillName, "history"],
    enabled: skillName !== null,
    queryFn: () => loadCustomSkillHistory(skillName!),
  });
}

export function useUpdateCustomSkill() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: ({ skillName, content }: { skillName: string; content: string }) =>
      updateCustomSkill(skillName, content),
    onSuccess: (_data, variables) => {
      void queryClient.invalidateQueries({ queryKey: ["skills"] });
      void queryClient.invalidateQueries({
        queryKey: ["skills", "custom", variables.skillName],
      });
      void queryClient.invalidateQueries({
        queryKey: ["skills", "custom", variables.skillName, "history"],
      });
    },
  });
}

export function useDeleteCustomSkill() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (skillName: string) => deleteCustomSkill(skillName),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ["skills"] });
    },
  });
}

export function useRollbackCustomSkill() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: ({ skillName, historyIndex }: { skillName: string; historyIndex: number }) =>
      rollbackCustomSkill(skillName, historyIndex),
    onSuccess: (_data, variables) => {
      void queryClient.invalidateQueries({ queryKey: ["skills"] });
      void queryClient.invalidateQueries({
        queryKey: ["skills", "custom", variables.skillName],
      });
      void queryClient.invalidateQueries({
        queryKey: ["skills", "custom", variables.skillName, "history"],
      });
    },
  });
}