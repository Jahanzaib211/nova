"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useEffect, useRef, useState } from "react";

import {
  type Job,
  type JobEvent,
  type JobSchedule,
  type ScheduleCreate,
  type ScheduleUpdate,
  cancelJob,
  createSchedule,
  deleteSchedule,
  jobEventStreamUrl,
  listJobEvents,
  listJobs,
  listSchedules,
  retryJob,
  updateSchedule,
} from "./api";
import { type JobStatus, isTerminalJobStatus } from "./types";

const JOBS_KEY = ["jobs"] as const;
const SCHEDULES_KEY = ["jobs", "schedules"] as const;

/** Poll while anything is still in flight; settle down once everything is terminal. */
export function pollIntervalFor(jobs: Job[] | undefined): number | false {
  if (!jobs) return false;
  return jobs.some((j) => !isTerminalJobStatus(j.status)) ? 3_000 : 30_000;
}

export function useJobs(status: JobStatus | null = null) {
  return useQuery({
    queryKey: [...JOBS_KEY, status ?? "all"],
    queryFn: () => listJobs({ status }),
    refetchInterval: (q) => pollIntervalFor(q.state.data),
  });
}

/**
 * Live events for one job. Opens the SSE stream while the job is running and
 * falls back to the JSON list once it is terminal (or if EventSource is
 * unavailable). Frames are the named `job_event` / `job_done` events, so an
 * older bundle that only listens to `message` ignores them by construction.
 */
export function useJobEvents(job: Job | null | undefined, enabled = true) {
  const [events, setEvents] = useState<JobEvent[]>([]);
  const [live, setLive] = useState(false);
  const lastSeq = useRef(0);
  const jobId = job?.id;
  const terminal = job ? isTerminalJobStatus(job.status) : true;

  useEffect(() => {
    setEvents([]);
    lastSeq.current = 0;
    setLive(false);
  }, [jobId]);

  useEffect(() => {
    if (!jobId || !enabled) return;
    let cancelled = false;
    if (terminal || typeof EventSource === "undefined") {
      void listJobEvents(jobId, 0).then(({ events: list }) => {
        if (!cancelled) setEvents(list);
      });
      return () => {
        cancelled = true;
      };
    }
    const es = new EventSource(jobEventStreamUrl(jobId, 0), {
      withCredentials: true,
    });
    setLive(true);
    es.addEventListener("job_event", (e) => {
      const ev = JSON.parse((e as MessageEvent<string>).data) as JobEvent;
      if (ev.seq <= lastSeq.current) return;
      lastSeq.current = ev.seq;
      setEvents((prev) => [...prev, ev]);
    });
    es.addEventListener("job_done", () => {
      setLive(false);
      es.close();
    });
    es.onerror = () => {
      setLive(false);
      es.close();
    };
    return () => {
      cancelled = true;
      setLive(false);
      es.close();
    };
  }, [jobId, terminal, enabled]);

  return { events, live };
}

export function useJobActions() {
  const qc = useQueryClient();
  const invalidate = () => qc.invalidateQueries({ queryKey: JOBS_KEY });
  const cancel = useMutation({ mutationFn: cancelJob, onSuccess: invalidate });
  const retry = useMutation({ mutationFn: retryJob, onSuccess: invalidate });
  return { cancel, retry };
}

export function useSchedules() {
  return useQuery({ queryKey: SCHEDULES_KEY, queryFn: listSchedules });
}

export function useScheduleActions() {
  const qc = useQueryClient();
  const invalidate = () => qc.invalidateQueries({ queryKey: SCHEDULES_KEY });
  const create = useMutation({
    mutationFn: (b: ScheduleCreate) => createSchedule(b),
    onSuccess: invalidate,
  });
  const update = useMutation({
    mutationFn: ({ id, body }: { id: string; body: ScheduleUpdate }) =>
      updateSchedule(id, body),
    onSuccess: invalidate,
  });
  const remove = useMutation({
    mutationFn: deleteSchedule,
    onSuccess: invalidate,
  });
  return { create, update, remove };
}

export type { Job, JobEvent, JobSchedule };
