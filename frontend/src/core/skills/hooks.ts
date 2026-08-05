import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

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
