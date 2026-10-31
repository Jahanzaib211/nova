import { cn } from "@/lib/utils";

import { TONE_CLASS, toneFor } from "../status";
import type { JobStatus } from "../types";

export function JobStatusBadge({
  status,
  className,
}: {
  status: JobStatus;
  className?: string;
}) {
  return (
    <span
      data-status={status}
      className={cn(
        "inline-flex h-6 items-center rounded-md px-2 font-mono text-[11px] font-medium tabular-nums",
        TONE_CLASS[toneFor(status)],
        className,
      )}
    >
      {status.replace("_", " ")}
    </span>
  );
}
