"use client";

import { RefreshCwIcon } from "lucide-react";
import { useState } from "react";
import { toast } from "sonner";

import { Button } from "@/components/ui/button";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { useI18n } from "@/core/i18n/hooks";
import { AgentsRegistryList } from "@/features/agents-registry/components/agents-registry-list";

import { JobEvents } from "../components/job-events";
import { JobProgress } from "../components/job-progress";
import { JobStatusBadge } from "../components/job-status-badge";
import { SchedulesPanel } from "../components/schedules-panel";
import { type Job, useJobActions, useJobs } from "../hooks";
import { canCancel, canRetry } from "../status";
import { formatRelative } from "../time";
import { JOB_STATUSES, type JobStatus } from "../types";

export function JobsPage() {
  const { t } = useI18n();
  const s = t.features.jobs;
  const [status, setStatus] = useState<JobStatus | null>(null);
  const {
    data: jobs,
    isLoading,
    isError,
    error,
    refetch,
    isFetching,
  } = useJobs(status);
  const { cancel, retry } = useJobActions();
  const [openId, setOpenId] = useState<string | null>(null);

  const act = (m: typeof cancel, id: string, ok: string) =>
    m
      .mutateAsync(id)
      .then(() => toast.success(ok))
      .catch((err: unknown) =>
        toast.error(err instanceof Error ? err.message : s.toasts.failed),
      );

  return (
    <div
      className="mx-auto flex w-full max-w-5xl flex-col gap-8 p-4 sm:p-6"
      data-testid="jobs-page"
    >
      <header className="flex flex-col gap-3 sm:flex-row sm:items-end sm:justify-between">
        <div className="min-w-0">
          <h1 className="text-lg font-semibold tracking-tight">{s.title}</h1>
          <p className="text-muted-foreground max-w-prose text-sm">
            {s.description}
          </p>
        </div>
        <div className="flex items-center gap-2">
          <Select
            value={status ?? "all"}
            onValueChange={(v) =>
              setStatus(v === "all" ? null : (v as JobStatus))
            }
          >
            <SelectTrigger
              className="w-full sm:w-[180px]"
              aria-label={s.columns.status}
            >
              <SelectValue />
            </SelectTrigger>
            <SelectContent>
              <SelectItem value="all">{s.filterAll}</SelectItem>
              {JOB_STATUSES.map((st) => (
                <SelectItem key={st} value={st}>
                  {st.replace("_", " ")}
                </SelectItem>
              ))}
            </SelectContent>
          </Select>
          <Button
            variant="outline"
            size="sm"
            onClick={() => void refetch()}
            disabled={isFetching}
            aria-label={s.actions.refresh}
          >
            <RefreshCwIcon
              className={isFetching ? "size-4 animate-spin" : "size-4"}
            />
          </Button>
        </div>
      </header>

      {isError ? (
        <p className="text-destructive text-sm" role="alert">
          {error instanceof Error ? error.message : s.toasts.failed}
        </p>
      ) : isLoading ? (
        <ul className="space-y-2" aria-busy="true">
          {[0, 1, 2].map((i) => (
            <li key={i} className="bg-muted/40 h-14 animate-pulse rounded-lg" />
          ))}
        </ul>
      ) : !jobs?.length ? (
        <p className="text-muted-foreground border-panel-border rounded-lg border border-dashed p-6 text-center text-sm">
          {s.empty}
        </p>
      ) : (
        <ul className="border-panel-border divide-panel-border divide-y rounded-lg border">
          {jobs.map((job) => (
            <JobRow
              key={job.id}
              job={job}
              open={openId === job.id}
              onToggle={() => setOpenId(openId === job.id ? null : job.id)}
              onCancel={() => act(cancel, job.id, s.toasts.cancelled)}
              onRetry={() => act(retry, job.id, s.toasts.retried)}
            />
          ))}
        </ul>
      )}

      <section data-testid="jobs-agents">
        <h2 className="mb-1 text-base font-semibold">
          {t.features.agentsRegistry.title}
        </h2>
        <AgentsRegistryList compact />
      </section>
      <SchedulesPanel />
    </div>
  );
}

function JobRow({
  job,
  open,
  onToggle,
  onCancel,
  onRetry,
}: {
  job: Job;
  open: boolean;
  onToggle: () => void;
  onCancel: () => void;
  onRetry: () => void;
}) {
  const { t } = useI18n();
  const s = t.features.jobs;
  return (
    <li
      className="flex flex-col gap-3 p-3"
      data-testid="job-row"
      data-status={job.status}
    >
      <div className="grid grid-cols-[minmax(0,1fr)_auto] items-center gap-3 sm:grid-cols-[minmax(0,2fr)_auto_minmax(0,2fr)_auto_auto]">
        <div className="min-w-0">
          <div className="truncate font-mono text-sm font-medium">
            {job.type}
          </div>
          <div className="text-muted-foreground text-[11px]">
            {job.queue} · {s.columns.created} {formatRelative(job.created_at)} ·{" "}
            {s.columns.attempts} {job.attempts}/{job.max_attempts}
          </div>
        </div>
        <JobStatusBadge status={job.status} />
        <div className="col-span-2 sm:col-span-1">
          <JobProgress
            pct={job.progress_pct}
            status={job.status}
            message={job.error ?? job.progress_message}
          />
        </div>
        <Button
          variant="ghost"
          size="sm"
          onClick={onToggle}
          aria-expanded={open}
        >
          {open ? s.actions.hideEvents : s.actions.showEvents}
        </Button>
        <div className="flex gap-1">
          {canCancel(job.status) && (
            <Button
              variant="outline"
              size="sm"
              onClick={onCancel}
              disabled={job.cancel_requested}
            >
              {s.actions.cancel}
            </Button>
          )}
          {canRetry(job.status) && (
            <Button variant="outline" size="sm" onClick={onRetry}>
              {s.actions.retry}
            </Button>
          )}
        </div>
      </div>
      {open && <JobEvents job={job} />}
    </li>
  );
}
