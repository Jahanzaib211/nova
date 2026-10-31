"use client";

import { RefreshCwIcon } from "lucide-react";

import { Button } from "@/components/ui/button";
import { SettingsSection } from "@/components/workspace/settings/settings-section";
import { useCapability } from "@/core/capabilities";
import { useI18n } from "@/core/i18n/hooks";
import { cn } from "@/lib/utils";

import { formatCell } from "../components/op-table";

/** Updates: what version of each moving part this deployment runs. */
export function UpdatesPage() {
  const { t } = useI18n();
  const s = t.features.console.updates;
  const versions = useCapability("updates.versions", {});

  return (
    <SettingsSection
      title={s.title}
      description={s.description}
      action={
        <Button
          variant="outline"
          size="sm"
          onClick={() => versions.refetch()}
          disabled={versions.isFetching}
        >
          <RefreshCwIcon
            className={cn("size-3.5", versions.isFetching && "animate-spin")}
          />
          {s.checkAgain}
        </Button>
      }
    >
      {versions.isLoading ? (
        <p className="text-muted-foreground text-sm">{t.common.loading}</p>
      ) : versions.isError ? (
        <p role="alert" className="text-destructive text-sm">
          {s.loadFailed}
        </p>
      ) : (
        <dl
          className="grid grid-cols-[auto_1fr] gap-x-6 gap-y-1 text-sm"
          data-testid="updates-versions"
        >
          {Object.entries(versions.data?.components ?? {}).map(([k, v]) => (
            <div key={k} className="contents">
              <dt className="text-muted-foreground">
                {s.labels[k as keyof typeof s.labels] ?? k}
              </dt>
              <dd className="font-mono text-xs">{formatCell(v)}</dd>
            </div>
          ))}
        </dl>
      )}
    </SettingsSection>
  );
}
