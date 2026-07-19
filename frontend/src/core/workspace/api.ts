import { fetch } from "@/core/api/fetcher";
import { getBackendBaseURL } from "@/core/config";

/** Snapshot summary returned by GET/POST /api/workspace/{thread_id}/... */
export interface WorkspaceProjectSummary {
  project_id: string;
  name: string;
  kind: string;
  root_path: string;
}

export interface WorkspaceSnapshotSummary {
  thread_workspace: string;
  repo_kind: string;
  primary_language: string;
  is_monorepo: boolean;
  project_count: number;
  symbol_count: number;
  command_count: number;
  node_count: number;
  edge_count: number;
  traversal_count: number;
  duration_ms: number;
  projects: WorkspaceProjectSummary[];
}

export type WorkspaceAvailability = "available" | "disabled" | "unindexed";

export class WorkspaceDisabledError extends Error {}
export class WorkspaceUnindexedError extends Error {}

function workspaceUrl(threadId: string, path: string): string {
  return `${getBackendBaseURL()}/api/workspace/${encodeURIComponent(threadId)}${path}`;
}

async function parseSnapshotResponse(res: Response): Promise<WorkspaceSnapshotSummary> {
  if (res.status === 403) throw new WorkspaceDisabledError("workspace intelligence disabled");
  if (res.status === 404) throw new WorkspaceUnindexedError("workspace not indexed");
  if (!res.ok) throw new Error(`workspace request failed: ${res.status}`);
  const body = (await res.json()) as { snapshot: WorkspaceSnapshotSummary };
  return body.snapshot;
}

export async function fetchWorkspaceSnapshot(threadId: string): Promise<WorkspaceSnapshotSummary> {
  const res = await fetch(workspaceUrl(threadId, "/snapshot"), {
    method: "GET",
    headers: { "Content-Type": "application/json" },
  });
  return parseSnapshotResponse(res);
}

export async function indexWorkspace(threadId: string, forceRefresh = false): Promise<WorkspaceSnapshotSummary> {
  const res = await fetch(workspaceUrl(threadId, "/index"), {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ force_refresh: forceRefresh }),
  });
  return parseSnapshotResponse(res);
}

export interface WorkspaceSymbol {
  name: string;
  kind: string;
  fqn: string;
  file_path: string;
  language: string;
}

export interface WorkspaceCommand {
  name: string;
  kind: string;
  project_id: string;
  argv: string[];
  description: string;
}

/** Symbols defined in one file (Files-tab outline). Empty on 403/404. */
export async function fetchFileSymbols(threadId: string, filePath: string): Promise<WorkspaceSymbol[]> {
  const res = await fetch(
    workspaceUrl(threadId, `/symbols?file=${encodeURIComponent(filePath)}`),
    { method: "GET", headers: { "Content-Type": "application/json" } },
  );
  if (!res.ok) return [];
  const body = (await res.json()) as { symbols: WorkspaceSymbol[] };
  return body.symbols ?? [];
}

/** Commands detected in the workspace (dev/test/lint...). Empty on 403/404. */
export async function fetchWorkspaceCommands(threadId: string): Promise<WorkspaceCommand[]> {
  const res = await fetch(workspaceUrl(threadId, "/commands"), {
    method: "GET",
    headers: { "Content-Type": "application/json" },
  });
  if (!res.ok) return [];
  const body = (await res.json()) as { commands: WorkspaceCommand[] };
  return body.commands ?? [];
}

export interface WorkspaceMetrics {
  scan: { count: number; avg_duration_ms: number };
  cache: { hits: number; misses: number; hit_rate: number };
  symbol_search: { count: number; avg_duration_ms: number };
}

/** Kernel metrics for the Privacy/status tab. Null on 403/404/error. */
export async function fetchWorkspaceMetrics(threadId: string): Promise<WorkspaceMetrics | null> {
  const res = await fetch(workspaceUrl(threadId, "/metrics"), {
    method: "GET",
    headers: { "Content-Type": "application/json" },
  });
  if (!res.ok) return null;
  const body = (await res.json()) as { metrics: WorkspaceMetrics };
  return body.metrics ?? null;
}

export interface WorkspaceImpact {
  scanned: boolean;
  symbols: WorkspaceSymbol[];
  /** Affected project ids (server sends ids, not objects). */
  projects: string[];
  commands: WorkspaceCommand[];
}

/** Blast radius of changing the given files. Null on 403/404/error. */
export async function fetchWorkspaceImpact(threadId: string, files: string[]): Promise<WorkspaceImpact | null> {
  const res = await fetch(workspaceUrl(threadId, "/impact"), {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ files }),
  });
  if (!res.ok) return null;
  return (await res.json()) as WorkspaceImpact;
}
