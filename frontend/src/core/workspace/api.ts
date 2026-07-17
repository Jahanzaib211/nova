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
