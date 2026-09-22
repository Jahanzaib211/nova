/** Presentation mapping for job statuses — the only place a status picks a colour. */
import type { JobStatus } from "./types";

export type StatusTone =
  | "muted"
  | "info"
  | "success"
  | "warning"
  | "destructive";

export function toneFor(status: JobStatus): StatusTone {
  switch (status) {
    case "queued":
      return "muted";
    case "leased":
    case "running":
      return "info";
    case "retrying":
      return "warning";
    case "succeeded":
      return "success";
    case "failed":
    case "dead_letter":
      return "destructive";
    case "cancelled":
      return "muted";
  }
}

export const TONE_CLASS: Record<StatusTone, string> = {
  muted: "bg-muted text-muted-foreground",
  info: "bg-info/15 text-info",
  success: "bg-success/15 text-success",
  warning: "bg-warning/15 text-warning-foreground dark:text-warning",
  destructive: "bg-destructive/15 text-destructive",
};

export function canCancel(status: JobStatus): boolean {
  return (
    status === "queued" ||
    status === "retrying" ||
    status === "leased" ||
    status === "running"
  );
}

export function canRetry(status: JobStatus): boolean {
  return status === "failed" || status === "dead_letter";
}
