/** REST client for /api/jobs (owner-scoped). Shapes mirror routers/jobs.py. */
import { fetch, getCsrfHeaders } from "@/core/api/fetcher";
import { getBackendBaseURL } from "@/core/config";

import type { JobEventType, JobStatus } from "./types";

export interface Job {
  id: string;
  type: string;
  queue: string;
  status: JobStatus;
  priority: number;
  payload: Record<string, unknown>;
  result: unknown;
  error: string | null;
  thread_id: string | null;
  attempts: number;
  max_attempts: number;
  progress_pct: number;
  progress_message: string | null;
  cancel_requested: boolean;
  schedule_id: string | null;
  run_after: string;
  created_at: string;
  started_at: string | null;
  finished_at: string | null;
}

export interface JobEvent {
  seq: number;
  type: JobEventType;
  payload: Record<string, unknown>;
  created_at: string;
}

export interface JobSchedule {
  id: string;
  name: string;
  type: string;
  queue: string;
  cron: string;
  timezone: string;
  payload: Record<string, unknown>;
  enabled: boolean;
  last_enqueued_for: string | null;
  next_run_at: string | null;
  created_at: string;
}

export interface ScheduleCreate {
  name: string;
  type: string;
  cron: string;
  payload?: Record<string, unknown>;
  queue?: string;
  timezone?: string;
  enabled?: boolean;
}

export type ScheduleUpdate = Partial<
  Pick<JobSchedule, "cron" | "payload" | "queue" | "timezone" | "enabled">
>;

function base(): string {
  return `${getBackendBaseURL()}/api/jobs`;
}

async function readJson<T>(res: Response, what: string): Promise<T> {
  if (!res.ok) {
    let detail = `${res.status} ${res.statusText}`;
    try {
      const body = (await res.json()) as { detail?: unknown };
      if (typeof body.detail === "string") detail = body.detail;
    } catch {
      // keep the status line
    }
    throw new Error(`${what}: ${detail}`);
  }
  return (await res.json()) as T;
}

function mutate(
  method: "POST" | "PATCH" | "DELETE",
  body?: unknown,
): RequestInit {
  return {
    method,
    headers: { "Content-Type": "application/json", ...getCsrfHeaders() },
    body: body === undefined ? undefined : JSON.stringify(body),
  };
}

export async function listJobs(
  params: { status?: JobStatus | null; limit?: number } = {},
): Promise<Job[]> {
  const qs = new URLSearchParams();
  if (params.status) qs.set("status", params.status);
  qs.set("limit", String(params.limit ?? 100));
  const res = await fetch(`${base()}?${qs.toString()}`);
  return (await readJson<{ jobs: Job[] }>(res, "Failed to load jobs")).jobs;
}

export async function getJob(id: string): Promise<Job> {
  return readJson<Job>(
    await fetch(`${base()}/${encodeURIComponent(id)}`),
    "Failed to load job",
  );
}

export async function listJobEvents(
  id: string,
  sinceSeq = 0,
): Promise<{ status: JobStatus; events: JobEvent[] }> {
  const res = await fetch(
    `${base()}/${encodeURIComponent(id)}/events?since_seq=${sinceSeq}`,
  );
  return readJson(res, "Failed to load job events");
}

export function jobEventStreamUrl(id: string, sinceSeq = 0): string {
  return `${base()}/${encodeURIComponent(id)}/events/stream?since_seq=${sinceSeq}`;
}

export async function cancelJob(
  id: string,
): Promise<{ ok: boolean; status: string }> {
  return readJson(
    await fetch(`${base()}/${encodeURIComponent(id)}/cancel`, mutate("POST")),
    "Cancel failed",
  );
}

export async function retryJob(
  id: string,
): Promise<{ ok: boolean; status: string }> {
  return readJson(
    await fetch(`${base()}/${encodeURIComponent(id)}/retry`, mutate("POST")),
    "Retry failed",
  );
}

export async function listSchedules(): Promise<JobSchedule[]> {
  return (
    await readJson<{ schedules: JobSchedule[] }>(
      await fetch(`${base()}/schedules`),
      "Failed to load schedules",
    )
  ).schedules;
}

export async function createSchedule(
  body: ScheduleCreate,
): Promise<JobSchedule> {
  return readJson(
    await fetch(`${base()}/schedules`, mutate("POST", body)),
    "Failed to create schedule",
  );
}

export async function updateSchedule(
  id: string,
  body: ScheduleUpdate,
): Promise<JobSchedule> {
  return readJson(
    await fetch(
      `${base()}/schedules/${encodeURIComponent(id)}`,
      mutate("PATCH", body),
    ),
    "Failed to update schedule",
  );
}

export async function deleteSchedule(id: string): Promise<void> {
  await readJson(
    await fetch(
      `${base()}/schedules/${encodeURIComponent(id)}`,
      mutate("DELETE"),
    ),
    "Failed to delete schedule",
  );
}
