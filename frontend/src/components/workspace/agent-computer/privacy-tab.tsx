"use client";

import { LoaderCircleIcon, ShieldIcon } from "lucide-react";

import { ScrollArea } from "@/components/ui/scroll-area";
import { Switch } from "@/components/ui/switch";
import { useI18n } from "@/core/i18n/hooks";
import { useIGINOStatus, useToggleIGINO } from "@/core/igino/hooks";
import { useWorkspaceMetrics } from "@/core/workspace/hooks";
import { cn } from "@/lib/utils";

export function PrivacyPanel({ threadId }: { threadId?: string }) {
  const { t } = useI18n();
  const { data: status, isLoading } = useIGINOStatus();
  const toggleMutation = useToggleIGINO();
  // Workspace kernel metrics (C10 item 10); null while the flag is off.
  const workspaceMetrics = useWorkspaceMetrics(threadId ?? null);

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
          <Switch
            checked={status.enabled}
            onCheckedChange={(checked) => toggleMutation.mutate(checked)}
            disabled={toggleMutation.isPending}
            aria-label={t.agentComputer.privacy.toggleLabel}
            data-testid="igino-toggle"
          />
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
            <StatusCard
              label={t.agentComputer.privacy.tor}
              status={
                status.tor_available
                  ? t.agentComputer.privacy.available
                  : t.agentComputer.privacy.unavailable
              }
              healthy={status.tor_available}
            />
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
              label={t.agentComputer.privacy.torUsage}
              value={status.audit?.tor_usage ?? 0}
            />
          </div>
        </div>

        {/* Workspace kernel (C10) — hidden until the backend flag is on */}
        {workspaceMetrics && (
          <div className="space-y-2">
            <h3 className="text-muted-foreground text-xs font-medium tracking-wider uppercase">
              {t.agentComputer.workspace.kernelTitle}
            </h3>
            <div className="grid grid-cols-1 gap-2 sm:grid-cols-3">
              <MetricCard label={t.agentComputer.workspace.kernelScans} value={workspaceMetrics.scan.count} />
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
