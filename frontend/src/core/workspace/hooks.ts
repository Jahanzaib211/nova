import { useQuery, useQueryClient } from "@tanstack/react-query";
import { useCallback, useState } from "react";

import {
  fetchWorkspaceSnapshot,
  indexWorkspace,
  WorkspaceDisabledError,
  WorkspaceUnindexedError,
  type WorkspaceAvailability,
  type WorkspaceSnapshotSummary,
} from "@/core/workspace/api";

export interface WorkspaceSnapshotState {
  availability: WorkspaceAvailability;
  snapshot: WorkspaceSnapshotSummary | null;
  isIndexing: boolean;
  /** Trigger a (re)index. No-op while one is already running or when disabled. */
  refresh: (force?: boolean) => void;
}

/**
 * Workspace snapshot for a thread (C10 Batch 1).
 *
 * Degrades gracefully by design:
 * - 403 (workspace.intelligence_enabled off) -> availability "disabled",
 *   callers render their pre-C10 UI and never retry in a loop.
 * - 404 (never indexed) -> availability "unindexed"; the first consumer
 *   mount triggers one index build, then the snapshot query refetches.
 * - Indexing is rate-limited server-side (cost tier), so refresh() is
 *   guarded against concurrent triggers client-side too.
 */
export function useWorkspaceSnapshot(
  threadId: string | null,
  enabled = true,
): WorkspaceSnapshotState {
  const queryClient = useQueryClient();
  const [isIndexing, setIsIndexing] = useState(false);

  const query = useQuery<WorkspaceSnapshotSummary, Error>({
    queryKey: ["workspace", "snapshot", threadId],
    queryFn: () => fetchWorkspaceSnapshot(threadId!),
    enabled: Boolean(threadId) && enabled,
    staleTime: 30_000,
    retry: (failureCount, error) => {
      if (error instanceof WorkspaceDisabledError) return false;
      if (error instanceof WorkspaceUnindexedError) return false;
      return failureCount < 2;
    },
  });

  const refresh = useCallback(
    (force = false) => {
      if (!threadId || isIndexing) return;
      if (query.error instanceof WorkspaceDisabledError) return;
      setIsIndexing(true);
      void indexWorkspace(threadId, force)
        .then((snapshot) => {
          queryClient.setQueryData(["workspace", "snapshot", threadId], snapshot);
        })
        .catch(() => {
          // disabled/unavailable: the snapshot query state already reflects it
        })
        .finally(() => setIsIndexing(false));
    },
    [threadId, isIndexing, query.error, queryClient],
  );

  let availability: WorkspaceAvailability = "available";
  if (query.error instanceof WorkspaceDisabledError) availability = "disabled";
  else if (query.error instanceof WorkspaceUnindexedError) availability = "unindexed";
  else if (query.isError) availability = "disabled";

  return {
    availability,
    snapshot: query.data ?? null,
    isIndexing,
    refresh,
  };
}
