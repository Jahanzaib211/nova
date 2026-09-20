"use client";

import Link from "next/link";
import { useState } from "react";

import { Button } from "@/components/ui/button";
import { SettingsSection } from "@/components/workspace/settings/settings-section";
import { useCapability, useCapabilityMutation } from "@/core/capabilities";
import { useI18n } from "@/core/i18n/hooks";

import { StatusDot } from "../components/op-table";

/** Runtimes: who can run a chat turn, under which account, and a live check.
 * The agent registry itself lives on /workspace/agents — not repeated here. */
export function AgentsPage() {
  const { t } = useI18n();
  const s = t.features.console.agents;
  const runtimes = useCapability("runtimes.list", {});
  const probe = useCapabilityMutation("runtimes.probe");
  const [probing, setProbing] = useState<string | null>(null);
  const [results, setResults] = useState<
    Record<string, { ok: boolean; detail: string; latency_ms?: number | null }>
  >({});

  const check = async (runtime: string) => {
    setProbing(runtime);
    try {
      const r = await probe.mutateAsync({ runtime });
      setResults((prev) => ({
        ...prev,
        [runtime]: {
          ok: r.ok,
          detail: r.detail ?? "",
          latency_ms: r.latency_ms ?? null,
        },
      }));
    } catch (e) {
      setResults((prev) => ({
        ...prev,
        [runtime]: { ok: false, detail: (e as Error).message },
      }));
    } finally {
      setProbing(null);
    }
  };

  return (
    <div className="space-y-8">
      <SettingsSection
        title={s.runtimesTitle}
        description={s.runtimesDescription}
      >
        {runtimes.isLoading ? (
          <p className="text-muted-foreground text-sm">{t.common.loading}</p>
        ) : runtimes.isError ? (
          <p role="alert" className="text-destructive text-sm">
            {s.loadFailed}
          </p>
        ) : (
          <>
            {runtimes.data && !runtimes.data.enabled ? (
              <p className="text-muted-foreground mb-3 text-sm">
                {s.disabledHint}
              </p>
            ) : null}
            <ul className="space-y-2" data-testid="runtimes-list">
              {(runtimes.data?.runtimes ?? []).map((r) => {
                const rt = r as {
                  id: string;
                  label: string;
                  description: string;
                  kind: string;
                  binary_on_path: boolean;
                  accounts: Array<{
                    id: string;
                    label: string;
                    available: boolean;
                    detail: string;
                  }>;
                };
                const res = results[rt.id];
                const ready =
                  rt.kind === "native" ||
                  (rt.binary_on_path && rt.accounts.some((a) => a.available));
                return (
                  <li key={rt.id} className="rounded-md border p-3">
                    <div className="flex flex-wrap items-center justify-between gap-2">
                      <div className="flex items-center gap-2">
                        <StatusDot
                          tone={
                            res ? (res.ok ? "ok" : "bad") : ready ? "ok" : "off"
                          }
                        />
                        <span className="font-medium">{rt.label}</span>
                        <span className="text-muted-foreground text-xs">
                          {rt.id}
                          {runtimes.data?.default === rt.id
                            ? ` · ${s.default}`
                            : ""}
                        </span>
                      </div>
                      {rt.kind === "acp" ? (
                        <Button
                          variant="outline"
                          size="sm"
                          disabled={probing !== null}
                          onClick={() => check(rt.id)}
                        >
                          {probing === rt.id ? s.checking : s.checkModel}
                        </Button>
                      ) : null}
                    </div>
                    <p className="text-muted-foreground mt-1 text-xs">
                      {rt.description}
                    </p>
                    <ul className="mt-2 flex flex-wrap gap-x-4 gap-y-1 text-xs">
                      {rt.accounts.map((a) => (
                        <li
                          key={a.id}
                          className="inline-flex items-center gap-1.5"
                          title={a.detail}
                        >
                          <StatusDot tone={a.available ? "ok" : "off"} />
                          {a.label}
                        </li>
                      ))}
                      {rt.kind === "acp" && !rt.binary_on_path ? (
                        <li className="text-warning">{s.binaryMissing}</li>
                      ) : null}
                    </ul>
                    {res ? (
                      <p
                        className={
                          res.ok
                            ? "text-success mt-2 text-xs"
                            : "text-destructive mt-2 text-xs"
                        }
                        data-testid={`probe-${rt.id}`}
                      >
                        {res.ok
                          ? `${s.probeOk}${res.latency_ms != null ? ` · ${res.latency_ms} ms` : ""}`
                          : `${s.probeFailed}: ${res.detail}`}
                      </p>
                    ) : null}
                  </li>
                );
              })}
            </ul>
            <p className="text-muted-foreground mt-3 text-xs">
              {s.modes}:{" "}
              {(runtimes.data?.modes ?? [])
                .map((m) => `${(m as { label: string }).label}`)
                .join(" · ")}
            </p>
          </>
        )}
      </SettingsSection>

      <p className="text-muted-foreground text-xs">
        {s.registryElsewhere}{" "}
        <Link
          href="/workspace/agents"
          className="text-primary underline-offset-2 hover:underline"
        >
          {s.registryLink}
        </Link>
      </p>
    </div>
  );
}
