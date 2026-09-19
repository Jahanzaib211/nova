import { cn } from "@/lib/utils";

import { TONE_CLASS, toneFor } from "../status";
import type { JobStatus } from "../types";

const BAR_CLASS: Record<ReturnType<typeof toneFor>, string> = {
  muted: "bg-muted-foreground/40",
  info: "bg-info",
  success: "bg-success",
  warning: "bg-warning",
  destructive: "bg-destructive",
};

export function JobProgress({
  pct,
  status,
  message,
}: {
  pct: number;
  status: JobStatus;
  message?: string | null;
}) {
  const clamped = Math.max(0, Math.min(100, pct));
  return (
    <div className="flex min-w-0 flex-col gap-1">
      <div
        role="progressbar"
        aria-valuemin={0}
        aria-valuemax={100}
        aria-valuenow={clamped}
        aria-valuetext={message ?? `${clamped}%`}
        className="bg-muted h-1.5 w-full overflow-hidden rounded-full"
      >
        <div
          className={cn(
            "h-full rounded-full transition-[width] duration-300",
            BAR_CLASS[toneFor(status)],
          )}
          style={{ width: `${clamped}%` }}
        />
      </div>
      <div
        className={cn(
          "truncate text-[11px]",
          TONE_CLASS[toneFor(status)]
            .split(" ")
            .find((c) => c.startsWith("text-")) ?? "text-muted-foreground",
        )}
      >
        {message ?? `${clamped}%`}
      </div>
    </div>
  );
}
