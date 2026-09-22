/**
 * Job runner vocabulary.
 *
 * Pinned to `contracts/job_status_contract.json` by
 * `tests/unit/contracts/job-status.contract.test.ts`; the backend
 * (`deerflow.jobs.status`) pins the same file.
 */

export const JOB_STATUSES = [
  "queued",
  "leased",
  "running",
  "retrying",
  "succeeded",
  "failed",
  "dead_letter",
  "cancelled",
] as const;
export type JobStatus = (typeof JOB_STATUSES)[number];

export const TERMINAL_JOB_STATUSES: readonly JobStatus[] = [
  "succeeded",
  "failed",
  "dead_letter",
  "cancelled",
];

export function isTerminalJobStatus(status: string): boolean {
  return (TERMINAL_JOB_STATUSES as readonly string[]).includes(status);
}

export const JOB_EVENT_TYPES = [
  "enqueued",
  "leased",
  "heartbeat",
  "progress",
  "log",
  "retry_scheduled",
  "released",
  "succeeded",
  "failed",
  "dead_lettered",
  "cancel_requested",
  "cancelled",
] as const;
export type JobEventType = (typeof JOB_EVENT_TYPES)[number];

export interface JobProgress {
  pct: number;
  message?: string | null;
  data?: Record<string, unknown> | null;
}
