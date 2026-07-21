"use client";

/**
 * RuntimeCapabilitiesBar — top-of-workspace status surface for the
 * Agent's Computer panel.
 *
 * Design principles (enterprise-grade):
 *   1. **Left-to-right hierarchy** by priority: STATUS first (health,
 *      circuits), then SKILLS (what the agent can do), then METRICS
 *      (counts). The eye lands on what matters when something breaks.
 *   2. **Quiet by default** — only the badges + counts. Hover reveals
 *      detail (descriptions, full lists, circuit IDs). Click reveals
 *      deep status.
 *   3. **Status-coloured elements are deliberate** — green/amber/red
 *      reserved for system health, never for decoration.
 *   4. **Single-line layout** that scrolls horizontally on narrow
 *      panels instead of wrapping (preserves the panel rhythm below).
 *   5. **Coexists** with the existing SkillLauncher dropdown — this bar
 *      is purely informational; the launcher remains the trigger.
 *
 * Driven by /api/runtime/capabilities (30s poll, TanStack Query).
 */

import {
  CheckCircle2Icon,
  CircuitBoardIcon,
  CogIcon,
  CpuIcon,
  LayersIcon,
  Loader2Icon,
  PlugZapIcon,
  ShieldIcon,
  SparklesIcon,
  TriangleAlertIcon,
  WrenchIcon,
  XCircleIcon,
  type BoxesIcon,
} from "lucide-react";
import { useEffect, useMemo, useState } from "react";

import { Badge } from "@/components/ui/badge";
import {
  Tooltip,
  TooltipContent,
  TooltipProvider,
  TooltipTrigger,
} from "@/components/ui/tooltip";
import { useI18n } from "@/core/i18n/hooks";
import { useIGINOStatus } from "@/core/igino/hooks";
import { useCapabilities, useOpenCircuitCount } from "@/core/runtime/hooks";
import type { CapabilitiesResponse } from "@/core/runtime/types";
import type { AgentActivityEvent } from "@/core/threads/hooks";
import { cn } from "@/lib/utils";

// Visual constants — fixed so the bar reads as one rhythm regardless
// of how many items are loaded.
const MAX_VISIBLE_SKILLS = 8;
const ROW_HEIGHT = "h-9";

function StatusDot({
  openCircuits,
  t,
}: {
  openCircuits: number;
  t: ReturnType<typeof useI18n>["t"];
}) {
  const { tone, label, Icon } = deriveStatusVisuals(openCircuits, t);
  return (
    <TooltipProvider delayDuration={150}>
      <Tooltip>
        <TooltipTrigger asChild>
          <button
            type="button"
            className={cn(
              "group hover:bg-muted/60 inline-flex shrink-0 items-center gap-1.5 rounded-md px-2 py-1 text-xs font-medium transition-colors",
              tone.bg,
            )}
            data-testid="runtime-status-button"
            data-state={label.state}
            aria-label={`Browser subsystem ${label.state}`}
          >
            <span
              className={cn(
                "relative inline-flex size-2 items-center justify-center rounded-full",
                tone.dot,
              )}
              aria-hidden
            >
              <span
                className={cn(
                  "absolute inset-0 animate-ping rounded-full opacity-60",
                  tone.dot,
                )}
                aria-hidden
              />
            </span>
            <Icon className={cn("size-3.5", tone.icon)} aria-hidden />
            <span className="text-foreground/90 tracking-tight">
              {label.text}
            </span>
          </button>
        </TooltipTrigger>
        <TooltipContent side="bottom" className="max-w-xs">
          <p className="font-medium">{label.tooltipTitle}</p>
          <p className="text-muted-foreground mt-1 text-xs">
            {label.tooltipBody}
          </p>
        </TooltipContent>
      </Tooltip>
    </TooltipProvider>
  );
}

function deriveStatusVisuals(
  openCircuits: number,
  t: ReturnType<typeof useI18n>["t"],
): {
  tone: { bg: string; dot: string; icon: string };
  label: {
    text: string;
    state: "healthy" | "degraded" | "critical";
    tooltipTitle: string;
    tooltipBody: string;
  };
  Icon: typeof CheckCircle2Icon;
} {
  if (openCircuits === 0) {
    return {
      tone: {
        bg: "bg-emerald-500/10",
        dot: "bg-emerald-500",
        icon: "text-emerald-600 dark:text-emerald-400",
      },
      label: {
        text: t.runtimeBar.status.healthy,
        state: "healthy",
        tooltipTitle: t.runtimeBar.status.healthyTitle,
        tooltipBody: t.runtimeBar.status.healthyBody,
      },
      Icon: CheckCircle2Icon,
    };
  }
  if (openCircuits <= 2) {
    return {
      tone: {
        bg: "bg-amber-500/10",
        dot: "bg-amber-500",
        icon: "text-amber-600 dark:text-amber-400",
      },
      label: {
        text: t.runtimeBar.status.degraded(openCircuits),
        state: "degraded",
        tooltipTitle: t.runtimeBar.status.degradedTitle(openCircuits),
        tooltipBody: t.runtimeBar.status.degradedBody,
      },
      Icon: TriangleAlertIcon,
    };
  }
  return {
    tone: {
      bg: "bg-red-500/10",
      dot: "bg-red-500",
      icon: "text-red-600 dark:text-red-400",
    },
    label: {
      text: t.runtimeBar.status.critical(openCircuits),
      state: "critical",
      tooltipTitle: t.runtimeBar.status.criticalTitle(openCircuits),
      tooltipBody: t.runtimeBar.status.criticalBody,
    },
    Icon: XCircleIcon,
  };
}

function SectionDivider() {
  return <span aria-hidden className="bg-border/60 mx-1 h-4 w-px shrink-0" />;
}

function SkillPill({
  skill,
}: {
  skill: CapabilitiesResponse["skills"][number];
}) {
  return (
    <TooltipProvider delayDuration={150}>
      <Tooltip>
        <TooltipTrigger asChild>
          <Badge
            variant={skill.enabled ? "secondary" : "outline"}
            className={cn(
              "h-6 cursor-default gap-1 rounded-md px-1.5 font-mono text-[11px] font-medium",
              skill.enabled
                ? "bg-secondary/60 hover:bg-secondary/80"
                : "opacity-60",
            )}
            data-testid={`runtime-skill-${skill.name}`}
            data-enabled={skill.enabled}
            data-category={skill.category}
          >
            <SparklesIcon className="size-2.5 opacity-70" aria-hidden />
            {skill.name}
          </Badge>
        </TooltipTrigger>
        <TooltipContent side="bottom" className="max-w-xs">
          <p className="font-medium">{skill.name}</p>
          {skill.description && (
            <p className="text-muted-foreground mt-1 text-xs leading-snug">
              {skill.description}
            </p>
          )}
          <p className="text-muted-foreground/70 mt-1 text-[10px] tracking-wide uppercase">
            {skill.category} · {skill.enabled ? "enabled" : "disabled"}
          </p>
        </TooltipContent>
      </Tooltip>
    </TooltipProvider>
  );
}

function SkillRail({
  skills,
  t,
}: {
  skills: CapabilitiesResponse["skills"];
  t: ReturnType<typeof useI18n>["t"];
}) {
  const enabled = skills.filter((s) => s.enabled);
  const total = skills.length;
  const overflow = total - MAX_VISIBLE_SKILLS;
  return (
    <TooltipProvider delayDuration={150}>
      <div className="flex min-w-0 items-center gap-1.5">
        <LayersIcon
          className="text-muted-foreground/70 size-3.5 shrink-0"
          aria-hidden
        />
        <span className="text-muted-foreground shrink-0 font-mono text-[11px] font-medium tabular-nums">
          {enabled.length}
          <span className="text-muted-foreground/50">/{total}</span>
        </span>
        <div className="flex min-w-0 items-center gap-1 overflow-hidden">
          {enabled.length === 0 ? (
            <span className="text-muted-foreground/70 text-[11px]">
              {t.runtimeBar.skills.none}
            </span>
          ) : (
            <>
              {enabled.slice(0, MAX_VISIBLE_SKILLS).map((s) => (
                <SkillPill key={s.name} skill={s} />
              ))}
              {overflow > 0 && (
                <Tooltip>
                  <TooltipTrigger asChild>
                    <span className="text-muted-foreground/80 cursor-default px-1 font-mono text-[11px]">
                      {t.runtimeBar.skills.overflow(overflow)}
                    </span>
                  </TooltipTrigger>
                  <TooltipContent side="bottom" className="max-w-sm">
                    <p className="font-medium">
                      {t.runtimeBar.skills.overflowHint}
                    </p>
                  </TooltipContent>
                </Tooltip>
              )}
            </>
          )}
        </div>
      </div>
    </TooltipProvider>
  );
}

function MetricCounter({
  icon: Icon,
  label,
  count,
  testId,
  detail,
}: {
  icon: typeof BoxesIcon;
  label: string;
  count: number;
  testId: string;
  detail?: string;
}) {
  const disabled = count === 0;
  return (
    <TooltipProvider delayDuration={150}>
      <Tooltip>
        <TooltipTrigger asChild>
          <span
            className={cn(
              "border-border/40 bg-background/50 hover:bg-muted/60 inline-flex h-6 shrink-0 items-center gap-1 rounded-md border px-1.5 font-mono text-[11px] transition-colors",
              disabled && "opacity-50",
            )}
            data-testid={testId}
            aria-label={`${label}: ${count}`}
          >
            <Icon className="text-muted-foreground/80 size-3" aria-hidden />
            <span className="text-foreground/80 tabular-nums">{count}</span>
            <span className="text-muted-foreground/70 text-[10px]">
              {label}
            </span>
          </span>
        </TooltipTrigger>
        <TooltipContent side="bottom" className="max-w-xs">
          <p className="font-medium">
            {count} {label.toLowerCase()}
          </p>
          {detail && (
            <p className="text-muted-foreground mt-1 text-xs">{detail}</p>
          )}
        </TooltipContent>
      </Tooltip>
    </TooltipProvider>
  );
}

function IGINOPill({ t }: { t: ReturnType<typeof useI18n>["t"] }) {
  const { data: status } = useIGINOStatus();
  if (!status?.enabled) return null;

  return (
    <TooltipProvider delayDuration={150}>
      <Tooltip>
        <TooltipTrigger asChild>
          <span
            className={cn(
              "border-border/40 bg-background/50 hover:bg-muted/60 inline-flex h-6 shrink-0 items-center gap-1 rounded-md border px-1.5 font-mono text-[11px] transition-colors",
            )}
            data-testid="runtime-igino-pill"
          >
            <ShieldIcon className="text-primary size-3" aria-hidden />
            <span className="text-foreground/80">
              {t.runtimeBar.igino.label}
            </span>
            {status.tor_enabled && (
              <span className="bg-primary/20 text-primary rounded px-1 text-[9px]">
                {t.a11y.tor}
              </span>
            )}
          </span>
        </TooltipTrigger>
        <TooltipContent side="bottom" className="max-w-xs">
          <p className="font-medium">{t.runtimeBar.igino.title}</p>
          <p className="text-muted-foreground mt-1 text-xs">
            {t.runtimeBar.igino.tooltip(
              status.searxng_healthy
                ? t.agentComputer.privacy.healthy
                : t.agentComputer.privacy.unhealthy,
              status.tor_available
                ? t.agentComputer.privacy.available
                : t.agentComputer.privacy.unavailable,
              `${status.cache.size}/${status.cache.max_size}`,
            )}
          </p>
        </TooltipContent>
      </Tooltip>
    </TooltipProvider>
  );
}

const INSTALLER_PATTERN = /(npm (i|install|run|yarn|pnpm|yarn)|pip install|bun install|pipenv|poetry install)/;
const INSTALLER_LABELS: Record<string, string> = {
  npm: "npm",
  yarn: "Yarn",
  pnpm: "pnpm",
  pip: "pip",
  bun: "Bun",
};

export function RuntimeCapabilitiesBar({
  className,
  sandboxEvents,
}: {
  className?: string;
  sandboxEvents?: AgentActivityEvent[];
}) {
  const { t } = useI18n();
  const { capabilities, isFetching, error } = useCapabilities();
  const openCircuits = useOpenCircuitCount();

  const installState = useMemo<{
    label: string;
    elapsed: number;
    running: boolean;
  } | null>(() => {
    if (!sandboxEvents?.length) return null;
    const last = sandboxEvents[sandboxEvents.length - 1];
    if (last?.status === "running" && INSTALLER_PATTERN.test(last.summary)) {
      const match = /(npm|yarn|pnpm|pip|bun)/.exec(last.summary);
      const _matched: string | undefined = match?.[1];
      const label = _matched ? (INSTALLER_LABELS[_matched] ?? _matched) : "Installing…";
      const elapsed = last.ts ? Math.floor((Date.now() - new Date(last.ts).getTime()) / 1000) : 0;
      return { label, elapsed, running: true };
    }
    return null;
  }, [sandboxEvents]);

  // Avoid hydration mismatch (server can't know capabilities).
  const [mounted, setMounted] = useState(false);
  useEffect(() => setMounted(true), []);

  // Offline / error state — distinct visual treatment.
  if (mounted && error && !capabilities) {
    return (
      <div
        className={cn(
          "bg-muted/30 text-muted-foreground border-border/40 flex items-center gap-2 border-b px-3 py-1.5 text-xs",
          className,
        )}
        data-testid="runtime-bar-offline"
        role="status"
      >
        <CircuitBoardIcon className="size-3.5" aria-hidden />
        <span className="font-medium">{t.runtimeBar.offline}</span>
        <span className="text-muted-foreground/70">
          · {t.runtimeBar.offlineHint}
        </span>
        {isFetching && (
          <Loader2Icon className="ml-auto size-3.5 animate-spin" aria-hidden />
        )}
      </div>
    );
  }

  // Loading state (before first response).
  if (!mounted || !capabilities) {
    return (
      <div
        className={cn(
          "bg-muted/10 border-border/40 flex items-center gap-2 border-b px-3 py-1.5 text-xs",
          className,
        )}
        aria-hidden
      >
        <span className={cn("flex items-center gap-2", ROW_HEIGHT)} />
      </div>
    );
  }

  const skills = capabilities.skills ?? [];
  const tools = capabilities.tools ?? [];
  const hooks = capabilities.hooks ?? [];
  const subagents = capabilities.subagents ?? [];
  const circuits = capabilities.circuits ?? [];

  return (
    <TooltipProvider delayDuration={150}>
      <div
        className={cn(
          "border-border/40 bg-background/80 supports-[backdrop-filter]:bg-background/60 flex items-center gap-2 overflow-x-auto border-b px-3 py-1.5 text-xs backdrop-blur-sm",
          ROW_HEIGHT,
          className,
        )}
        data-testid="runtime-capabilities-bar"
        role="status"
        aria-label={t.a11y.runtimeCapabilities}
      >
        <StatusDot openCircuits={openCircuits} t={t} />

        <SectionDivider />

        <SkillRail skills={skills} t={t} />

        <SectionDivider />

        <MetricCounter
          icon={WrenchIcon}
          label={t.runtimeBar.metrics.tools}
          count={tools.length}
          testId="runtime-tools-pill"
          detail={t.runtimeBar.metrics.toolsDetail}
        />
        <MetricCounter
          icon={CpuIcon}
          label={t.runtimeBar.metrics.subagents}
          count={subagents.length}
          testId="runtime-subagents-pill"
          detail={
            capabilities.server?.max_concurrent_subagents
              ? `${t.runtimeBar.metrics.subagentsDetail} ${t.runtimeBar.metrics.subagentsConcurrency(capabilities.server.max_concurrent_subagents)}`
              : t.runtimeBar.metrics.subagentsDetail
          }
        />
        <MetricCounter
          icon={CogIcon}
          label={t.runtimeBar.metrics.hooks}
          count={hooks.length}
          testId="runtime-hooks-pill"
          detail={t.runtimeBar.metrics.hooksDetail}
        />

        <SectionDivider />

        <IGINOPill t={t} />

        {openCircuits > 0 && (
          <Tooltip>
            <TooltipTrigger asChild>
              <span
                className="bg-destructive/10 text-destructive border-destructive/30 inline-flex h-6 shrink-0 items-center gap-1 rounded-md border px-1.5 font-mono text-[11px] font-medium"
                data-testid="runtime-open-circuits-pill"
              >
                <PlugZapIcon className="size-3" aria-hidden />
                <span className="tabular-nums">{openCircuits}</span>
                <span>{t.a11y.open}</span>
              </span>
            </TooltipTrigger>
            <TooltipContent side="bottom" className="max-w-sm">
              <p className="font-medium">
                {t.runtimeBar.circuits.open(openCircuits)}
              </p>
              <ul className="text-muted-foreground mt-1 space-y-0.5 text-xs">
                {circuits
                  .filter((c) => c.state === "open")
                  .slice(0, 5)
                  .map((c) => (
                    <li key={c.thread_id} className="font-mono">
                      · {c.thread_id}
                    </li>
                  ))}
                {circuits.filter((c) => c.state === "open").length > 5 && (
                  <li>… and more</li>
                )}
              </ul>
              <p className="text-muted-foreground/70 mt-1.5 text-[10px]">
                {t.runtimeBar.circuits.autoRecovers}
              </p>
            </TooltipContent>
          </Tooltip>
        )}

        {installState && (
          <span
            className="bg-amber-500/10 text-amber-600 dark:text-amber-400 border-amber-500/30 inline-flex h-6 shrink-0 items-center gap-1 rounded-md border px-1.5 font-mono text-[11px] font-medium"
            data-testid="runtime-installing-pill"
          >
            <Loader2Icon className="size-3 animate-spin" aria-hidden />
            <span>Installing {installState.label}</span>
            {installState.elapsed > 0 && (
              <span className="tabular-nums">{installState.elapsed}s</span>
            )}
          </span>
        )}

        <div className="ml-auto flex items-center gap-2">
          {capabilities.server.version &&
            capabilities.server.version !== "dev" && (
              <span className="text-muted-foreground/60 font-mono text-[10px] tracking-wider uppercase">
                v{capabilities.server.version}
              </span>
            )}
          {isFetching ? (
            <Loader2Icon
              className="text-muted-foreground/60 size-3.5 shrink-0 animate-spin"
              aria-label="refreshing"
            />
          ) : (
            <span
              className="inline-flex size-1.5 shrink-0 rounded-full bg-emerald-500/60"
              aria-label="ready"
              data-testid="runtime-bar-ready-dot"
            />
          )}
        </div>
      </div>
    </TooltipProvider>
  );
}
