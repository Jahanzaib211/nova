import { useMutation, useQuery } from "@tanstack/react-query";

import { createShareLink, loadSharedThread, revokeShareLink } from "./api";

export function useCreateShareLink() {
  return useMutation({
    mutationFn: (threadId: string) => createShareLink(threadId),
  });
}

export function useRevokeShareLink() {
  return useMutation({
    mutationFn: (threadId: string) => revokeShareLink(threadId),
  });
}

export function useSharedThread(token: string) {
  return useQuery({
    queryKey: ["sharing", "thread", token],
    queryFn: () => loadSharedThread(token),
    retry: 1,
  });
}