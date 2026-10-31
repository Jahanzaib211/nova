"use client";

import { SettingsSection } from "@/components/workspace/settings/settings-section";
import { useCapability } from "@/core/capabilities";
import { useI18n } from "@/core/i18n/hooks";

import { OpTable, tsToLocal } from "../components/op-table";

/** Cloud workers: the job-runner processes. The jobs they run, agent tasks
 * included, are listed in Settings › Jobs — not repeated here. */
export function WorkersPage() {
  const { t } = useI18n();
  const s = t.features.console.workers;
  const workers = useCapability(
    "jobs.workers",
    {},
    { refetchInterval: 15_000 },
  );

  return (
    <div className="space-y-8">
      <SettingsSection title={s.title} description={s.description}>
        {workers.isLoading ? (
          <p className="text-muted-foreground text-sm">{t.common.loading}</p>
        ) : workers.isError ? (
          <p role="alert" className="text-destructive text-sm">
            {s.unavailable}
          </p>
        ) : (
          <OpTable
            empty={s.empty}
            keyOf={(r) => String(r.worker_id ?? r.id)}
            rows={workers.data?.items ?? []}
            columns={[
              {
                key: "worker_id",
                label: s.colWorker,
                render: (r) => (
                  <span className="font-mono text-xs">
                    {String(r.worker_id ?? r.id)}
                  </span>
                ),
              },
              { key: "hostname", label: s.colHost },
              {
                key: "queues",
                label: s.colQueues,
                render: (r) =>
                  Array.isArray(r.queues)
                    ? (r.queues as string[]).join(", ")
                    : "—",
              },
              {
                key: "current_job_ids",
                label: s.colRunning,
                render: (r) =>
                  String(
                    Array.isArray(r.current_job_ids)
                      ? (r.current_job_ids as string[]).length
                      : 0,
                  ),
              },
              {
                key: "last_heartbeat_at",
                label: s.colHeartbeat,
                render: (r) => tsToLocal(r.last_seen_at ?? r.last_heartbeat_at),
              },
            ]}
          />
        )}
      </SettingsSection>
    </div>
  );
}
