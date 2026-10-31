/** Presentation for campaign statuses and stat tiles — the only place a status picks a colour. */
import type { CampaignStats } from "./api";
import type { CampaignStatus } from "./types";

export type Tone = "muted" | "info" | "success" | "warning" | "destructive";

export function campaignTone(status: CampaignStatus): Tone {
  switch (status) {
    case "draft":
      return "muted";
    case "scheduled":
    case "sending":
      return "info";
    case "paused":
      return "warning";
    case "completed":
      return "success";
    case "cancelled":
      return "muted";
    case "failed":
      return "destructive";
  }
}

export const TONE_CLASS: Record<Tone, string> = {
  muted: "bg-muted text-muted-foreground",
  info: "bg-info/15 text-info",
  success: "bg-success/15 text-success",
  warning: "bg-warning/15 text-warning-foreground dark:text-warning",
  destructive: "bg-destructive/15 text-destructive",
};

export const CAMPAIGN_ACTIONS: Record<
  CampaignStatus,
  Array<"send-now" | "pause" | "resume" | "cancel">
> = {
  draft: ["send-now"],
  scheduled: ["send-now", "cancel"],
  sending: ["pause", "cancel"],
  paused: ["resume", "cancel"],
  completed: [],
  cancelled: [],
  failed: [],
};

/** A rate as a percentage of sent, or null when nothing was sent yet (never 0% of nothing). */
export function rate(
  part: number | undefined,
  sent: number | undefined,
): number | null {
  if (!sent) return null;
  return Math.round(((part ?? 0) / sent) * 1000) / 10;
}

export function progressPct(
  stats: Partial<CampaignStats> | undefined,
): number | null {
  const total = stats?.recipients ?? 0;
  if (!total) return null;
  const done = total - (stats?.queued ?? 0) - (stats?.sending ?? 0);
  return Math.max(0, Math.min(100, Math.round((done / total) * 100)));
}
