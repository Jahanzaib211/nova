"use client";

import { RefreshCwIcon } from "lucide-react";

import { Button } from "@/components/ui/button";
import { SettingsSection } from "@/components/workspace/settings/settings-section";
import { useI18n } from "@/core/i18n/hooks";
import { cn } from "@/lib/utils";

import { IntegrationCard } from "../components/integration-card";
import { useIntegrations, useProbeIntegrations } from "../hooks";
import { DOT_CLASS, groupIntegrations, summarize, toneFor } from "../status";
import type { IntegrationStatus } from "../types";

const SUMMARY_ORDER: IntegrationStatus[] = [
  "healthy",
  "degraded",
  "down",
  "unknown",
  "disabled",
];

export function IntegrationsSettingsPage() {
  const { t } = useI18n();
  const s = t.features.integrations;
  const query = useIntegrations();
  const probe = useProbeIntegrations();
  const items = query.data?.integrations ?? [];
  const counts = summarize(items);
  const groups = groupIntegrations(items);
  const probingId = probe.one.isPending ? probe.one.variables : null;

  return (
    <div className="space-y-8">
      <SettingsSection
        title={s.title}
        description={s.description}
        action={
          <Button
            variant="outline"
            size="sm"
            disabled={
              probe.all.isPending || query.isLoading || items.length === 0
            }
            onClick={() => probe.all.mutate()}
          >
            <RefreshCwIcon
              className={cn("size-3.5", probe.all.isPending && "animate-spin")}
            />
            {s.probeAll}
          </Button>
        }
      >
        {query.isLoading ? (
          <div className="grid gap-3 sm:grid-cols-2" aria-busy="true">
            {[0, 1, 2, 3].map((i) => (
              <div
                key={i}
                className="bg-muted/40 h-28 animate-pulse rounded-lg"
              />
            ))}
          </div>
        ) : query.isError ? (
          <p role="alert" className="text-destructive text-sm">
            {s.loadFailed}
          </p>
        ) : query.data && !query.data.enabled ? (
          <p className="text-muted-foreground text-sm">{s.disabledHint}</p>
        ) : items.length === 0 ? (
          <p className="text-muted-foreground text-sm">{s.empty}</p>
        ) : (
          <ul
            className="flex flex-wrap gap-x-4 gap-y-1 text-xs"
            data-testid="integrations-summary"
          >
            {SUMMARY_ORDER.filter((k) => counts[k] > 0).map((k) => (
              <li
                key={k}
                className="inline-flex items-center gap-1.5 tabular-nums"
              >
                <span
                  className={cn("size-2 rounded-full", DOT_CLASS[toneFor(k)])}
                  aria-hidden
                />
                {counts[k]} {s.status[k]}
              </li>
            ))}
          </ul>
        )}
      </SettingsSection>

      {groups.map((group) => (
        <SettingsSection key={group.id} title={s.groups[group.id]}>
          <div className="grid gap-3 sm:grid-cols-2">
            {group.items.map((item) => (
              <IntegrationCard
                key={item.id}
                item={item}
                probing={probingId === item.id}
                onProbe={(id) => probe.one.mutate(id)}
              />
            ))}
          </div>
        </SettingsSection>
      ))}
    </div>
  );
}
