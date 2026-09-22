"use client";

import { SettingsSection } from "@/components/workspace/settings/settings-section";
import { useCapability, useCapabilityOps } from "@/core/capabilities";
import { useI18n } from "@/core/i18n/hooks";

import { StatusDot } from "../components/op-table";

/** Labs: server feature switches, each tied to a config.yaml section. */
export function LabsPage() {
  const { t } = useI18n();
  const s = t.features.console.labs;
  const flags = useCapability("features.get", {});
  const ops = useCapabilityOps();
  const modules = ops.data?.snapshot.modules ?? [];

  return (
    <SettingsSection title={s.title} description={s.description}>
      {flags.isLoading ? (
        <p className="text-muted-foreground text-sm">{t.common.loading}</p>
      ) : flags.isError ? (
        <p role="alert" className="text-destructive text-sm">
          {s.loadFailed}
        </p>
      ) : (
        <ul className="space-y-2" data-testid="labs-flags">
          {Object.entries(flags.data?.flags ?? {}).map(([key, on]) => {
            const mod = modules.find((m) => m.flag === key);
            return (
              <li
                key={key}
                className="flex items-start gap-3 rounded-md border p-3"
              >
                <span className="mt-1.5">
                  <StatusDot tone={on ? "ok" : "off"} />
                </span>
                <div className="min-w-0 flex-1">
                  <div className="flex items-baseline justify-between gap-2">
                    <span className="font-medium">{mod?.title ?? key}</span>
                    <code className="text-muted-foreground font-mono text-xs">
                      {key}
                    </code>
                  </div>
                  <p className="text-muted-foreground text-xs">
                    {mod?.description ?? ""}{" "}
                    {mod?.config_key
                      ? `${s.configuredIn} ${mod.config_key}.enabled`
                      : ""}
                  </p>
                </div>
                <span className="text-xs">{on ? s.on : s.off}</span>
              </li>
            );
          })}
        </ul>
      )}
      <p className="text-muted-foreground mt-3 text-xs">{s.howToToggle}</p>
    </SettingsSection>
  );
}
