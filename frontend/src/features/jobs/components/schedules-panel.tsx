"use client";

import { Trash2Icon } from "lucide-react";
import { useState } from "react";
import { toast } from "sonner";

import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Switch } from "@/components/ui/switch";
import { useI18n } from "@/core/i18n/hooks";

import { useScheduleActions, useSchedules } from "../hooks";
import { formatRelative } from "../time";

export function parsePayload(raw: string): Record<string, unknown> | null {
  if (raw.trim() === "") return {};
  try {
    const value: unknown = JSON.parse(raw);
    return typeof value === "object" && value !== null && !Array.isArray(value)
      ? (value as Record<string, unknown>)
      : null;
  } catch {
    return null;
  }
}

export function SchedulesPanel() {
  const { t } = useI18n();
  const s = t.features.jobs.schedules;
  const { data: schedules, isLoading } = useSchedules();
  const { create, update, remove } = useScheduleActions();
  const [form, setForm] = useState({
    name: "",
    type: "",
    cron: "0 * * * *",
    timezone: "UTC",
    payload: "",
  });

  const submit = async (e: React.FormEvent) => {
    e.preventDefault();
    const payload = parsePayload(form.payload);
    if (payload === null) {
      toast.error(s.invalidPayload);
      return;
    }
    try {
      await create.mutateAsync({
        name: form.name.trim(),
        type: form.type.trim(),
        cron: form.cron.trim(),
        timezone: form.timezone.trim() || "UTC",
        payload,
      });
      toast.success(s.created);
      setForm({
        name: "",
        type: "",
        cron: "0 * * * *",
        timezone: "UTC",
        payload: "",
      });
    } catch (err) {
      toast.error(
        err instanceof Error ? err.message : t.features.jobs.toasts.failed,
      );
    }
  };

  return (
    <section className="space-y-4" data-testid="job-schedules">
      <header>
        <h2 className="text-base font-semibold">{s.title}</h2>
        <p className="text-muted-foreground text-sm">{s.description}</p>
      </header>

      {isLoading ? null : !schedules?.length ? (
        <p className="text-muted-foreground border-panel-border rounded-lg border border-dashed p-4 text-sm">
          {s.empty}
        </p>
      ) : (
        <ul className="border-panel-border divide-panel-border divide-y rounded-lg border">
          {schedules.map((sched) => (
            <li
              key={sched.id}
              className="flex flex-col gap-2 p-3 sm:flex-row sm:items-center sm:gap-4"
            >
              <div className="min-w-0 flex-1">
                <div className="flex items-center gap-2">
                  <span className="truncate font-medium">{sched.name}</span>
                  <span className="text-muted-foreground font-mono text-[11px]">
                    {sched.type}
                  </span>
                </div>
                <div className="text-muted-foreground mt-0.5 font-mono text-[11px]">
                  {sched.cron} · {sched.timezone} · {s.nextRun}:{" "}
                  {formatRelative(sched.next_run_at)}
                </div>
              </div>
              <div className="flex items-center gap-3">
                <label className="flex items-center gap-2 text-xs">
                  <Switch
                    checked={sched.enabled}
                    aria-label={s.enabled}
                    onCheckedChange={(enabled) =>
                      update
                        .mutateAsync({ id: sched.id, body: { enabled } })
                        .then(() => toast.success(s.updated))
                        .catch((err: unknown) =>
                          toast.error(
                            err instanceof Error
                              ? err.message
                              : t.features.jobs.toasts.failed,
                          ),
                        )
                    }
                  />
                  {s.enabled}
                </label>
                <Button
                  variant="ghost"
                  size="icon-sm"
                  aria-label={s.delete}
                  onClick={() =>
                    remove
                      .mutateAsync(sched.id)
                      .then(() => toast.success(s.deleted))
                      .catch((err: unknown) =>
                        toast.error(
                          err instanceof Error
                            ? err.message
                            : t.features.jobs.toasts.failed,
                        ),
                      )
                  }
                >
                  <Trash2Icon className="size-4" />
                </Button>
              </div>
            </li>
          ))}
        </ul>
      )}

      <form
        onSubmit={submit}
        className="border-panel-border grid gap-3 rounded-lg border p-3 sm:grid-cols-2"
        aria-label={s.create}
      >
        <Input
          required
          placeholder={s.name}
          value={form.name}
          onChange={(e) => setForm({ ...form, name: e.target.value })}
        />
        <Input
          required
          placeholder={s.type}
          value={form.type}
          onChange={(e) => setForm({ ...form, type: e.target.value })}
        />
        <Input
          required
          placeholder={s.cron}
          value={form.cron}
          onChange={(e) => setForm({ ...form, cron: e.target.value })}
          className="font-mono"
        />
        <Input
          placeholder={s.timezone}
          value={form.timezone}
          onChange={(e) => setForm({ ...form, timezone: e.target.value })}
        />
        <Input
          placeholder={s.payload}
          value={form.payload}
          onChange={(e) => setForm({ ...form, payload: e.target.value })}
          className="font-mono sm:col-span-2"
        />
        <div className="sm:col-span-2">
          <Button type="submit" size="sm" disabled={create.isPending}>
            {s.create}
          </Button>
        </div>
      </form>
    </section>
  );
}
