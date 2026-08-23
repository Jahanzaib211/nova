"use client";

import { Badge } from "@/components/ui/badge";
import { Skeleton } from "@/components/ui/skeleton";
import { useRuntimeConfig } from "@/core/api/runtime-config";
import { useI18n } from "@/core/i18n/hooks";

import { SettingsSection } from "./settings-section";

export function RuntimeSettingsPage() {
  const { t } = useI18n();
  const { data, isLoading, error } = useRuntimeConfig();

  if (isLoading) {
    return (
      <SettingsSection
        title={t.settings.runtime.title}
        description={t.settings.runtime.description}
      >
        <Skeleton className="h-32 w-full" />
      </SettingsSection>
    );
  }

  if (error || !data) {
    return (
      <SettingsSection
        title={t.settings.runtime.title}
        description={t.settings.runtime.description}
      >
        <p className="text-muted-foreground rounded-md border border-amber-200 bg-amber-50 p-3 text-sm dark:border-amber-800 dark:bg-amber-950/50">
          {t.settings.runtime.unavailable}
        </p>
      </SettingsSection>
    );
  }

  return (
    <div className="space-y-8">
      <SettingsSection
        title={t.settings.runtime.summarization.title}
        description={t.settings.runtime.summarization.description}
      >
        <div className="space-y-4">
          <div className="flex items-center justify-between">
            <span className="text-sm font-medium">
              {t.settings.runtime.summarization.enabled}
            </span>
            <Badge
              variant={data.summarization.enabled ? "default" : "secondary"}
            >
              {data.summarization.enabled
                ? t.settings.runtime.on
                : t.settings.runtime.off}
            </Badge>
          </div>
          <Row
            label={t.settings.runtime.summarization.model}
            value={data.summarization.model_name ?? t.settings.runtime.notSet}
          />
          <Row
            label={t.settings.runtime.summarization.trigger}
            value={
              data.summarization.trigger_type !== null
                ? `${data.summarization.trigger_type}: ${data.summarization.trigger_value ?? "?"}`
                : t.settings.runtime.notSet
            }
          />
          <Row
            label={t.settings.runtime.summarization.keep}
            value={
              data.summarization.keep_type !== null
                ? `${data.summarization.keep_type}: ${data.summarization.keep_value ?? "?"}`
                : t.settings.runtime.notSet
            }
          />
        </div>
      </SettingsSection>

      <SettingsSection
        title={t.settings.runtime.subagents.title}
        description={t.settings.runtime.subagents.description}
      >
        <div className="space-y-4">
          <Row
            label={t.settings.runtime.subagents.timeout}
            value={
              data.subagents.default_timeout_seconds !== null
                ? `${data.subagents.default_timeout_seconds}s`
                : t.settings.runtime.notSet
            }
          />
          <Row
            label={t.settings.runtime.subagents.maxTurns}
            value={
              data.subagents.max_turns !== null
                ? String(data.subagents.max_turns)
                : t.settings.runtime.notSet
            }
          />
          <Row
            label={t.settings.runtime.subagents.customAgents}
            value={String(data.subagents.custom_agents_count)}
          />
        </div>
      </SettingsSection>

      <SettingsSection
        title={t.settings.runtime.guardrails.title}
        description={t.settings.runtime.guardrails.description}
      >
        <div className="space-y-4">
          <div className="flex items-center justify-between">
            <span className="text-sm font-medium">
              {t.settings.runtime.guardrails.enabled}
            </span>
            <Badge variant={data.guardrails.enabled ? "default" : "secondary"}>
              {data.guardrails.enabled
                ? t.settings.runtime.on
                : t.settings.runtime.off}
            </Badge>
          </div>
          <Row
            label={t.settings.runtime.guardrails.failClosed}
            value={
              data.guardrails.fail_closed
                ? t.settings.runtime.yes
                : t.settings.runtime.no
            }
          />
          <Row
            label={t.settings.runtime.guardrails.provider}
            value={data.guardrails.provider_class ?? t.settings.runtime.notSet}
          />
          <Row
            label={t.settings.runtime.guardrails.passport}
            value={data.guardrails.passport ?? t.settings.runtime.notSet}
          />
        </div>
      </SettingsSection>

      <p className="text-muted-foreground text-xs">
        {t.settings.runtime.editHint}
      </p>
    </div>
  );
}

function Row({ label, value }: { label: string; value: string }) {
  return (
    <div className="flex items-center justify-between border-b pb-2 last:border-0">
      <span className="text-muted-foreground text-sm">{label}</span>
      <span className="font-mono text-xs">{value}</span>
    </div>
  );
}
