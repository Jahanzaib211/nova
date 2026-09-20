"use client";

import { useState } from "react";

import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { SettingsSection } from "@/components/workspace/settings/settings-section";
import { useCapability, useCapabilityMutation } from "@/core/capabilities";
import { useI18n } from "@/core/i18n/hooks";

import { OpTable, StatusDot, tsToLocal } from "../components/op-table";

/** Automation: cron schedules on the job runner. */
export function AutomationPage() {
  const { t } = useI18n();
  const s = t.features.console.automation;
  const schedules = useCapability("jobs.schedules", {});
  const upsert = useCapabilityMutation("jobs.schedule_upsert", [
    "jobs.schedules",
  ]);
  const remove = useCapabilityMutation("jobs.schedule_delete", [
    "jobs.schedules",
  ]);
  const [name, setName] = useState("");
  const [type, setType] = useState("");
  const [cron, setCron] = useState("0 * * * *");
  const [payload, setPayload] = useState("{}");
  const [error, setError] = useState<string | null>(null);

  return (
    <SettingsSection title={s.title} description={s.description}>
      <form
        className="mb-4 grid gap-2 sm:grid-cols-[1fr_1fr_1fr_1fr_auto]"
        onSubmit={(e) => {
          e.preventDefault();
          setError(null);
          let parsed: Record<string, unknown> = {};
          try {
            parsed = payload.trim()
              ? (JSON.parse(payload) as Record<string, unknown>)
              : {};
          } catch {
            setError(s.badPayload);
            return;
          }
          upsert.mutate(
            {
              name: name.trim(),
              type: type.trim(),
              cron: cron.trim(),
              payload: parsed,
            },
            { onError: (err) => setError(err.message) },
          );
        }}
      >
        <Input
          value={name}
          onChange={(e) => setName(e.target.value)}
          placeholder={s.name}
          aria-label={s.name}
        />
        <Input
          value={type}
          onChange={(e) => setType(e.target.value)}
          placeholder={s.type}
          aria-label={s.type}
        />
        <Input
          value={cron}
          onChange={(e) => setCron(e.target.value)}
          placeholder="0 * * * *"
          aria-label={s.cron}
        />
        <Input
          value={payload}
          onChange={(e) => setPayload(e.target.value)}
          placeholder="{}"
          aria-label={s.payload}
        />
        <Button
          type="submit"
          size="sm"
          disabled={
            upsert.isPending || !name.trim() || !type.trim() || !cron.trim()
          }
        >
          {s.save}
        </Button>
      </form>
      {error ? (
        <p role="alert" className="text-destructive mb-3 text-sm">
          {error}
        </p>
      ) : null}
      {schedules.isLoading ? (
        <p className="text-muted-foreground text-sm">{t.common.loading}</p>
      ) : schedules.isError ? (
        <p role="alert" className="text-destructive text-sm">
          {s.unavailable}
        </p>
      ) : (
        <OpTable
          empty={s.empty}
          keyOf={(r) => String(r.id)}
          rows={schedules.data?.items ?? []}
          columns={[
            {
              key: "enabled",
              label: "",
              render: (r) => <StatusDot tone={r.enabled ? "ok" : "off"} />,
            },
            { key: "name", label: s.name },
            {
              key: "type",
              label: s.type,
              render: (r) => (
                <code className="font-mono text-xs">
                  {String(r.type ?? r.job_type)}
                </code>
              ),
            },
            {
              key: "cron",
              label: s.cron,
              render: (r) => (
                <code className="font-mono text-xs">{String(r.cron)}</code>
              ),
            },
            {
              key: "next_run_at",
              label: s.nextRun,
              render: (r) => tsToLocal(r.next_run_at),
            },
            {
              key: "actions",
              label: "",
              render: (r) => (
                <Button
                  variant="ghost"
                  size="sm"
                  onClick={() => remove.mutate({ schedule_id: String(r.id) })}
                  disabled={remove.isPending}
                >
                  {s.delete}
                </Button>
              ),
            },
          ]}
        />
      )}
    </SettingsSection>
  );
}
