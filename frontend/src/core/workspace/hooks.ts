import { useQuery, useQueryClient } from "@tanstack/react-query";
import { useCallback, useEffect, useRef, useState } from "react";

import { getBackendBaseURL } from "@/core/config";
import {
  fetchFileSymbols,
  fetchWorkspaceCommands,
  fetchWorkspaceImpact,
  fetchWorkspaceMetrics,
  fetchWorkspaceSnapshot,
  indexWorkspace,
  WorkspaceDisabledError,
  WorkspaceUnindexedError,
  type WorkspaceAvailability,
  type WorkspaceCommand,
  type WorkspaceImpact,
  type WorkspaceMetrics,
  type WorkspaceSnapshotSummary,
  type WorkspaceSymbol,
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
          queryClient.setQueryData(
            ["workspace", "snapshot", threadId],
            snapshot,
          );
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
  else if (query.error instanceof WorkspaceUnindexedError)
    availability = "unindexed";
  else if (query.isError) availability = "disabled";

  return {
    availability,
    snapshot: query.data ?? null,
    isIndexing,
    refresh,
  };
}

/**
 * Symbols defined in one file — fetched lazily when a code-file row is
 * expanded in the Files tab. Cached per (thread, file); 403/404 resolve to
 * an empty list so the tree renders identically pre-flag.
 */
export function useFileSymbols(
  threadId: string | null,
  filePath: string | null,
  enabled = true,
): WorkspaceSymbol[] {
  const query = useQuery<WorkspaceSymbol[]>({
    queryKey: ["workspace", "symbols", threadId, filePath],
    queryFn: () => fetchFileSymbols(threadId!, filePath!),
    enabled: Boolean(threadId && filePath) && enabled,
    staleTime: 30_000,
    retry: false,
  });
  return query.data ?? [];
}

/** Commands detected in the workspace (dev/test/lint...). Empty pre-flag. */
export function useWorkspaceCommands(
  threadId: string | null,
  enabled = true,
): WorkspaceCommand[] {
  const query = useQuery<WorkspaceCommand[]>({
    queryKey: ["workspace", "commands", threadId],
    queryFn: () => fetchWorkspaceCommands(threadId!),
    enabled: Boolean(threadId) && enabled,
    staleTime: 60_000,
    retry: false,
  });
  return query.data ?? [];
}

/** Kernel metrics for the Privacy tab. Null while the flag is off. */
export function useWorkspaceMetrics(
  threadId: string | null,
  enabled = true,
): WorkspaceMetrics | null {
  const query = useQuery<WorkspaceMetrics | null>({
    queryKey: ["workspace", "metrics", threadId],
    queryFn: () => fetchWorkspaceMetrics(threadId!),
    enabled: Boolean(threadId) && enabled,
    staleTime: 30_000,
    retry: false,
  });
  return query.data ?? null;
}

/** Impact preview for one file (C10 item 9). Null while unavailable. */
export function useFileImpact(
  threadId: string | null,
  filePath: string | null,
  enabled = true,
): WorkspaceImpact | null {
  const query = useQuery<WorkspaceImpact | null>({
    queryKey: ["workspace", "impact", threadId, filePath],
    queryFn: () => fetchWorkspaceImpact(threadId!, [filePath!]),
    enabled: Boolean(threadId && filePath) && enabled,
    staleTime: 30_000,
    retry: false,
  });
  return query.data ?? null;
}

// ── useWorkspaceEvents ──────────────────────────────────────
// Live SSE feed of WIK activity (scans, plans, cache hits/misses).
// Same EventSource pattern as useSandboxLogs (core/sandbox/hooks.ts),
// but the backend emits named event types rather than default `message`
// frames, so each type needs its own addEventListener.

export type WorkspaceEventType =
  | "WorkspaceScanned"
  | "PlanBuilt"
  | "CacheHit"
  | "CacheMiss";

export interface WorkspaceScannedPayload {
  root_path: string;
  occurred_at: string;
  scan_id: string;
  file_count: number;
  project_count: number;
  symbol_count: number;
  command_count: number;
  scan_duration_ms: number;
  language_distribution: Record<string, number>;
}

export interface PlanBuiltPayload {
  root_path: string;
  occurred_at: string;
  plan_id: string;
  plan_title: string;
  step_count: number;
  plan_valid: boolean;
  risk_level: string;
  symbols_found: string[];
}

export interface CacheHitPayload {
  root_path: string;
  occurred_at: string;
  cache_key: string;
  hit_count: number;
}

export interface CacheMissPayload {
  root_path: string;
  occurred_at: string;
  cache_key: string;
}

export type WorkspaceLiveEvent =
  | { type: "WorkspaceScanned"; data: WorkspaceScannedPayload }
  | { type: "PlanBuilt"; data: PlanBuiltPayload }
  | { type: "CacheHit"; data: CacheHitPayload }
  | { type: "CacheMiss"; data: CacheMissPayload };

const MAX_WORKSPACE_EVENTS = 100;
const WORKSPACE_EVENT_TYPES: WorkspaceEventType[] = [
  "WorkspaceScanned",
  "PlanBuilt",
  "CacheHit",
  "CacheMiss",
];

/**
 * Live WIK event feed for a thread (bus -> SSE bridge, real-time). Returns
 * a bounded, newest-last list; empty (never an error state) while the
 * workspace flag is off or the thread has no live connection yet — callers
 * degrade the same way the other workspace hooks do pre-flag.
 */
/** Mirrors MAX_OPEN_FAILURES in core/sandbox/hooks.ts; same reasoning. */
const MAX_WORKSPACE_OPEN_FAILURES = 3;

export function useWorkspaceEvents(
  threadId: string | null,
  enabled = true,
): WorkspaceLiveEvent[] {
  const [events, setEvents] = useState<WorkspaceLiveEvent[]>([]);
  const esRef = useRef<EventSource | null>(null);
  const failuresRef = useRef(0);

  useEffect(() => {
    setEvents([]);
  }, [threadId]);

  useEffect(() => {
    if (!threadId || !enabled || typeof EventSource === "undefined") return;

    esRef.current?.close();

    const url = `${getBackendBaseURL()}/api/workspace/${encodeURIComponent(threadId)}/events`;
    const es = new EventSource(url, { withCredentials: true });
    esRef.current = es;

    const append =
      (type: WorkspaceEventType) => (event: MessageEvent<string>) => {
        try {
          const data = JSON.parse(event.data);
          setEvents((prev) => {
            const next = [...prev, { type, data } as WorkspaceLiveEvent];
            return next.length > MAX_WORKSPACE_EVENTS
              ? next.slice(next.length - MAX_WORKSPACE_EVENTS)
              : next;
          });
        } catch {
          // malformed frame — skip silently, same tolerance as useSandboxLogs
        }
      };

    failuresRef.current = 0;

    const listeners = WORKSPACE_EVENT_TYPES.map((type) => {
      const inner = append(type);
      // Register the wrapper, and keep a reference to the SAME function so the
      // cleanup below actually detaches it.
      const handler = (event: MessageEvent) => {
        // A delivered frame proves the stream is healthy, so earlier failures
        // were transient and must not count toward the cap.
        failuresRef.current = 0;
        inner(event);
      };
      es.addEventListener(type, handler as EventListener);
      return { type, handler: handler as EventListener };
    });

    es.onerror = () => {
      // 403 when workspace.intelligence_enabled is off is permanent, not
      // transient, and an EventSource left alone retries it every few seconds
      // forever with a console error each time. Same cap as useSandboxLogs:
      // give up once it has failed repeatedly without ever delivering a frame.
      failuresRef.current += 1;
      if (failuresRef.current >= MAX_WORKSPACE_OPEN_FAILURES) {
        es.close();
        esRef.current = null;
      }
    };

    return () => {
      for (const { type, handler } of listeners)
        es.removeEventListener(type, handler);
      es.close();
      esRef.current = null;
    };
  }, [threadId, enabled]);

  return events;
}
