"use client";

import { RefreshCwIcon } from "lucide-react";

import { Button } from "@/components/ui/button";
import { SettingsSection } from "@/components/workspace/settings/settings-section";
import {
  useCapability,
  useCapabilityMutation,
  useCapabilityOps,
} from "@/core/capabilities";
import { useI18n } from "@/core/i18n/hooks";
import { cn } from "@/lib/utils";

import { OpTable, StatusDot, formatCell } from "../components/op-table";

/** Gateway: every module's live status, the ACP adapters, and versions. */
export function GatewayPage() {
  const { t } = useI18n();
  const s = t.features.console.gateway;
  const ops = useCapabilityOps();
  const acp = useCapability("acp.agents", {});
  const versions = useCapability("updates.versions", {});
  const probe = useCapabilityMutation("integrations.probe", [
    "integrations.list",
  ]);

  const modules = ops.data?.snapshot.modules ?? [];
  const status = ops.data?.status ?? {};

  return (
    <div className="space-y-8">
      <SettingsSection
        title={s.title}
        description={s.description}
        action={
          <Button
            variant="outline"
            size="sm"
            onClick={() => ops.refetch()}
            disabled={ops.isFetching}
          >
            <RefreshCwIcon
              className={cn("size-3.5", ops.isFetching && "animate-spin")}
            />
            {s.refresh}
          </Button>
        }
      >
        {ops.isLoading ? (
          <p className="text-muted-foreground text-sm">{t.common.loading}</p>
        ) : ops.isError ? (
          <p role="alert" className="text-destructive text-sm">
            {s.loadFailed}
          </p>
        ) : (
          <ul
            className="grid gap-2 sm:grid-cols-2"
            data-testid="gateway-modules"
          >
            {modules.map((m) => {
              const st = status[m.id];
              const tone = !st
                ? "off"
                : !st.configured
                  ? "off"
                  : st.healthy
                    ? "ok"
                    : "warn";
              return (
                <li
                  key={m.id}
                  className="flex items-start gap-3 rounded-md border p-3"
                >
                  <span className="mt-1.5">
                    <StatusDot tone={tone} />
                  </span>
                  <div className="min-w-0 flex-1">
                    <div className="flex items-baseline justify-between gap-2">
                      <span className="font-medium">{m.title}</span>
                      <span className="text-muted-foreground text-xs tabular-nums">
                        {m.operations.length} {s.ops}
                      </span>
                    </div>
                    <p className="text-muted-foreground truncate text-xs">
                      {st?.detail ?? m.description}
                    </p>
                  </div>
                </li>
              );
            })}
          </ul>
        )}
      </SettingsSection>

      <SettingsSection title={s.acpTitle} description={s.acpDescription}>
        {acp.isLoading ? (
          <p className="text-muted-foreground text-sm">{t.common.loading}</p>
        ) : (
          <OpTable
            empty={s.acpEmpty}
            keyOf={(r) => String(r.name)}
            rows={acp.data?.items ?? []}
            columns={[
              { key: "name", label: s.colAgent },
              {
                key: "binary_on_path",
                label: s.colBinary,
                render: (r) => (
                  <StatusDot tone={r.binary_on_path ? "ok" : "bad"} />
                ),
              },
              { key: "model", label: s.colModel },
              {
                key: "permission_policy",
                label: s.colPolicy,
                render: (r) => {
                  const p = r.permission_policy as
                    | { allow_kinds?: string[]; deny_kinds?: string[] }
                    | undefined;
                  return (
                    <span className="text-xs">
                      {s.allow}: {(p?.allow_kinds ?? []).join(", ") || "—"} ·{" "}
                      {s.deny}: {(p?.deny_kinds ?? []).join(", ") || "—"}
                    </span>
                  );
                },
              },
            ]}
          />
        )}
      </SettingsSection>

      <SettingsSection
        title={s.versionsTitle}
        description={s.versionsDescription}
      >
        {versions.isLoading ? (
          <p className="text-muted-foreground text-sm">{t.common.loading}</p>
        ) : (
          <dl className="grid grid-cols-[auto_1fr] gap-x-6 gap-y-1 text-sm">
            {Object.entries(versions.data?.components ?? {}).map(([k, v]) => (
              <div key={k} className="contents">
                <dt className="text-muted-foreground">{k}</dt>
                <dd className="font-mono text-xs">{formatCell(v)}</dd>
              </div>
            ))}
          </dl>
        )}
      </SettingsSection>
      <span className="hidden">{probe.isPending ? "" : ""}</span>
    </div>
  );
}
