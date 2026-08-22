"use client";

import { LoaderCircleIcon, ShieldIcon } from "lucide-react";
import { useState } from "react";

import { ScrollArea } from "@/components/ui/scroll-area";
import { useI18n } from "@/core/i18n/hooks";
import { useIGINOStatus, useTestIGINOCapability } from "@/core/igino/hooks";
import { useWorkspaceMetrics } from "@/core/workspace/hooks";
import { cn } from "@/lib/utils";

/** The request path, in the order it actually runs. Named for what each step
    does rather than for its provider: the provider is an implementation detail
    that changed twice in one day, the job did not.

    The hints are the whole point of this list. The first three read pages
    somebody already named; only the last one discovers a page you did not
    know about, and for most of this panel's life the UI called one of the
    others "Crawler" and described a capability Nova did not have. */
const PIPELINE: Array<{ tool: string; label: string; hint: string }> = [
  { tool: "web_search", label: "Search", hint: "finds pages" },
  { tool: "web_fetch", label: "Fetch", hint: "reads one page" },
  {
    tool: "web_fetch_many",
    label: "Fetch many",
    hint: "reads several pages you name, at once",
  },
  {
    tool: "web_crawl",
    label: "Crawl",
    hint: "starts at one page and follows its links",
  },
];

export function PrivacyPanel({
  threadId,
  active = true,
}: {
  threadId?: string;
  /** Gates the metrics query so it doesn't poll while the tab is hidden but stays mounted. */
  active?: boolean;
}) {
  const { t } = useI18n();
  const { data: status, isLoading } = useIGINOStatus(active);
  const testMutation = useTestIGINOCapability();
  const [testing, setTesting] = useState<string | null>(null);
  const [testResults, setTestResults] = useState<
    Record<string, { ok: boolean; detail: string; duration_ms: number }>
  >({});

  const runTest = (tool: string) => {
    setTesting(tool);
    testMutation.mutate(tool, {
      onSuccess: (r) =>
        setTestResults((prev) => ({
          ...prev,
          [tool]: { ok: r.ok, detail: r.detail, duration_ms: r.duration_ms },
        })),
      // A failed request is itself a result worth showing -- the panel exists
      // to surface breakage, so swallowing the error would defeat the button.
      onError: (e: unknown) =>
        setTestResults((prev) => ({
          ...prev,
          [tool]: {
            ok: false,
            detail: e instanceof Error ? e.message : "request failed",
            duration_ms: 0,
          },
        })),
      onSettled: () => setTesting(null),
    });
  };
  // Workspace kernel metrics (C10 item 10); null while the flag is off.
  const workspaceMetrics = useWorkspaceMetrics(threadId ?? null, active);

  if (isLoading) {
    return (
      <div className="flex h-full items-center justify-center p-4">
        <LoaderCircleIcon className="text-muted-foreground h-4 w-4 animate-spin" />
      </div>
    );
  }

  if (!status) {
    return (
      <div className="text-muted-foreground flex h-full items-center justify-center p-4 text-sm">
        {t.common.loading}
      </div>
    );
  }

  // When Recon is off the status endpoint returns `{enabled: false}` and
  // nothing else, so every field below reads as undefined: SearXNG rendered
  // "unhealthy", and cache/audit rendered real-looking zeros. A switched-off
  // feature looked like a broken one. The endpoint has always documented this
  // shape as the cue for a "feature off" state -- this is that state.
  if (!status.enabled) {
    return (
      <div className="flex h-full flex-col items-center justify-center gap-3 p-6 text-center">
        <ShieldIcon className="text-muted-foreground h-5 w-5" />
        <div className="space-y-1">
          <p className="text-sm font-medium">
            {t.agentComputer.privacy.disabledTitle}
          </p>
          <p className="text-muted-foreground max-w-sm text-xs">
            {t.agentComputer.privacy.disabledBody}
          </p>
        </div>
      </div>
    );
  }

  return (
    <ScrollArea className="h-full">
      <div className="space-y-4 p-4">
        {/* Header */}
        <div className="flex items-center justify-between">
          <div className="flex items-center gap-2">
            <ShieldIcon className="text-primary h-4 w-4" />
            <span className="text-sm font-medium">
              {t.agentComputer.privacy.title}
            </span>
          </div>
        </div>

        {/* Source Health */}
        <div className="space-y-2">
          <h3 className="text-muted-foreground text-xs font-medium tracking-wider uppercase">
            {t.agentComputer.privacy.sourceHealth}
          </h3>
          <div className="grid grid-cols-1 gap-2 sm:grid-cols-2">
            <StatusCard
              label={t.agentComputer.privacy.searxng}
              status={
                status.searxng_healthy
                  ? t.agentComputer.privacy.healthy
                  : t.agentComputer.privacy.unhealthy
              }
              healthy={status.searxng_healthy}
            />
            {/* One card per web capability, named by the job it does. These
                are four different things that fail independently:
                  search      finds pages                  (SearXNG)
                  fetch       reads one named page         (Browserless)
                  fetch_many  reads many named pages       (Crawl4AI)
                  crawl       follows links from one page  (Nova's BFS)
                A single "crawler" row hid which of them was down, and named
                the wrong one: Browserless renders a single URL and follows
                nothing, and Crawl4AI's REST API refuses deep_crawl_strategy
                from an untrusted request, which is every request we can make.
                Only the last row follows links, and it does so in Nova's own
                loop -- which is also where its page/depth/domain caps and its
                robots.txt check live. */}
            {(status.web ?? []).map((cap) => (
              <StatusCard
                key={cap.tool}
                label={
                  cap.tool === "web_fetch"
                    ? t.agentComputer.privacy.fetch
                    : cap.tool === "web_fetch_many"
                      ? t.agentComputer.privacy.fetchMany
                      : t.agentComputer.privacy.crawl
                }
                status={`${cap.provider} · ${
                  cap.healthy
                    ? t.agentComputer.privacy.healthy
                    : t.agentComputer.privacy.unhealthy
                }`}
                healthy={cap.healthy}
              />
            ))}
          </div>
        </div>

        {/* Cache Stats */}
        <div className="space-y-2">
          <h3 className="text-muted-foreground text-xs font-medium tracking-wider uppercase">
            {t.agentComputer.privacy.cache}
          </h3>
          <div className="grid grid-cols-1 gap-2 sm:grid-cols-3">
            <MetricCard
              label={t.agentComputer.privacy.size}
              value={`${status.cache?.size ?? 0}/${status.cache?.max_size ?? 0}`}
            />
            <MetricCard
              label={t.agentComputer.privacy.hitRate}
              value={`${((status.cache?.hit_rate ?? 0) * 100).toFixed(1)}%`}
            />
            <MetricCard
              label={t.agentComputer.privacy.ttl}
              value={`${status.cache?.ttl_s ?? 0}s`}
            />
          </div>
        </div>

        {/* Audit Stats */}
        <div className="space-y-2">
          <h3 className="text-muted-foreground text-xs font-medium tracking-wider uppercase">
            {t.agentComputer.privacy.audit}
          </h3>
          <div className="grid grid-cols-1 gap-2 sm:grid-cols-3">
            <MetricCard
              label={t.agentComputer.privacy.total}
              value={status.audit?.total_records ?? 0}
            />
            <MetricCard
              label={t.agentComputer.privacy.errors}
              value={status.audit?.errors ?? 0}
            />
            <MetricCard
              label={t.agentComputer.privacy.fetches}
              value={status.audit?.fetches ?? 0}
            />
          </div>
        </div>

        {/* Crawler — the fetch half of the pipeline, broken out from search.
            Both used to collapse into one `errors` number, which said nothing
            about which half was down. */}
        <div className="space-y-2">
          <h3 className="text-muted-foreground text-xs font-medium tracking-wider uppercase">
            {t.agentComputer.privacy.pipeline}
          </h3>
          <div className="grid grid-cols-1 gap-2 sm:grid-cols-3">
            <MetricCard
              label={t.agentComputer.privacy.fetches}
              value={status.audit?.fetches ?? 0}
            />
            <MetricCard
              label={t.agentComputer.privacy.errors}
              value={status.audit?.fetch_errors ?? 0}
            />
            <MetricCard
              label={t.agentComputer.privacy.avgFetch}
              value={`${status.audit?.avg_fetch_ms ?? 0}ms`}
            />
          </div>
          {/* Pipeline, in the order a request actually flows through it, with
              a self-test per step. The internal base URLs used to be printed
              here: they leak the compose topology to anyone with the panel
              open, and a user cannot act on "http://browserless:3000" anyway.
              A button that performs a real search or fetch answers the question
              the URL was standing in for. */}
          <div className="space-y-1">
            {PIPELINE.map((step, i) => {
              const cap = (status.web ?? []).find((c) => c.tool === step.tool);
              const healthy =
                step.tool === "web_search"
                  ? status.searxng_healthy
                  : (cap?.healthy ?? false);
              const result = testResults[step.tool];
              return (
                <div
                  key={step.tool}
                  className="flex items-center gap-2 rounded-md border px-2 py-1.5"
                >
                  <span className="text-muted-foreground w-4 shrink-0 text-[10px]">
                    {i + 1}
                  </span>
                  <div className="min-w-0 flex-1">
                    <div className="truncate text-xs font-medium">
                      {step.label}
                    </div>
                    <div className="text-muted-foreground truncate text-[10px]">
                      {result
                        ? `${result.ok ? "ok" : "failed"} · ${result.detail} · ${result.duration_ms}ms`
                        : step.hint}
                    </div>
                  </div>
                  <span
                    className={cn(
                      "shrink-0 text-[10px]",
                      healthy ? "text-emerald-400" : "text-amber-400",
                    )}
                  >
                    ●
                  </span>
                  <button
                    type="button"
                    onClick={() => runTest(step.tool)}
                    disabled={testing === step.tool}
                    className="hover:bg-muted shrink-0 rounded border px-2 py-0.5 text-[10px] disabled:opacity-50"
                  >
                    {testing === step.tool
                      ? t.common.loading
                      : t.agentComputer.privacy.test}
                  </button>
                </div>
              );
            })}
          </div>
        </div>

        {/* Capabilities. Replaces a master on/off switch that wrote nothing:
            each of these is configured independently by environment and fails
            independently, so one aggregate "enabled" could never describe the
            real state. Read-only on purpose -- four controls that also did
            nothing would be a worse lie than the one they replace. */}
        {status.features?.length ? (
          <div className="space-y-2">
            <h3 className="text-muted-foreground text-xs font-medium tracking-wider uppercase">
              {t.agentComputer.privacy.capabilities}
            </h3>
            <div className="space-y-1">
              {status.features.map((f) => (
                <div
                  key={f.key}
                  className="flex items-center justify-between gap-2 rounded-md border px-2 py-1.5"
                >
                  <div className="min-w-0">
                    <div className="truncate text-xs font-medium">
                      {f.label}
                    </div>
                    <div className="text-muted-foreground truncate text-[10px]">
                      {f.env}
                      {f.detail ? ` — ${f.detail}` : ""}
                    </div>
                  </div>
                  <span
                    className={cn(
                      "shrink-0 text-[10px] font-medium tracking-wide uppercase",
                      f.enabled ? "text-emerald-400" : "text-muted-foreground",
                    )}
                  >
                    {f.enabled
                      ? t.agentComputer.privacy.on
                      : t.agentComputer.privacy.off}
                  </span>
                </div>
              ))}
            </div>
          </div>
        ) : null}

        {/* Workspace kernel (C10) — hidden until the backend flag is on */}
        {workspaceMetrics && (
          <div className="space-y-2">
            <h3 className="text-muted-foreground text-xs font-medium tracking-wider uppercase">
              {t.agentComputer.workspace.kernelTitle}
            </h3>
            <div className="grid grid-cols-1 gap-2 sm:grid-cols-3">
              <MetricCard
                label={t.agentComputer.workspace.kernelScans}
                value={workspaceMetrics.scan.count}
              />
              <MetricCard
                label={t.agentComputer.workspace.kernelAvgScan}
                value={`${workspaceMetrics.scan.avg_duration_ms.toFixed(0)}ms`}
              />
              <MetricCard
                label={t.agentComputer.workspace.kernelCacheHits}
                value={`${(workspaceMetrics.cache.hit_rate * 100).toFixed(0)}%`}
              />
            </div>
          </div>
        )}

        {/* Error */}
        {status.error && (
          <div className="bg-destructive/10 text-destructive rounded-md p-3 text-xs">
            {status.error}
          </div>
        )}
      </div>
    </ScrollArea>
  );
}

function StatusCard({
  label,
  status,
  healthy,
}: {
  label: string;
  status: string;
  healthy: boolean;
}) {
  return (
    <div className="border-border/50 rounded-md border p-2">
      <div className="text-muted-foreground text-xs">{label}</div>
      <div
        className={cn(
          "mt-1 text-sm font-medium",
          healthy ? "text-emerald-400" : "text-amber-400",
        )}
      >
        {status}
      </div>
    </div>
  );
}

function MetricCard({
  label,
  value,
}: {
  label: string;
  value: string | number;
}) {
  return (
    <div className="border-border/50 rounded-md border p-2">
      <div className="text-muted-foreground text-xs">{label}</div>
      <div className="mt-1 text-sm font-medium">{value}</div>
    </div>
  );
}

// ──────────────────────────────────────────────────────────
// Main panel
