"use client";

import { useI18n } from "@/core/i18n/hooks";

import { type Job, useJobEvents } from "../hooks";
import { formatRelative } from "../time";

export function JobEvents({ job }: { job: Job }) {
  const { t } = useI18n();
  const { events, live } = useJobEvents(job);
  const s = t.features.jobs.events;
  return (
    <div
      className="border-panel-border bg-panel rounded-lg border p-3 text-xs"
      data-testid="job-events"
    >
      <div className="mb-2 flex items-center gap-2">
        <span className="font-medium">{s.title}</span>
        {live && (
          <span className="bg-info/15 text-info inline-flex items-center gap-1 rounded-md px-1.5 py-0.5 font-mono text-[10px]">
            <span
              className="bg-info size-1.5 animate-pulse rounded-full"
              aria-hidden
            />
            {s.live}
          </span>
        )}
      </div>
      {events.length === 0 ? (
        <p className="text-muted-foreground">{s.empty}</p>
      ) : (
        <ol className="space-y-1 font-mono">
          {events.map((e) => (
            <li key={e.seq} className="flex gap-3">
              <span className="text-muted-foreground w-24 shrink-0">
                {formatRelative(e.created_at)}
              </span>
              <span className="w-28 shrink-0">{e.type}</span>
              <span className="text-muted-foreground min-w-0 truncate">
                {summarise(e.payload)}
              </span>
            </li>
          ))}
        </ol>
      )}
    </div>
  );
}

function summarise(payload: Record<string, unknown>): string {
  if (typeof payload.message === "string") return payload.message;
  if (typeof payload.error === "string") return payload.error;
  if (typeof payload.pct === "number") return `${payload.pct}%`;
  const keys = Object.keys(payload);
  return keys.length ? JSON.stringify(payload) : "";
}
