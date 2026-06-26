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
import { useEffect, useState } from "react";

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
import { cn } from "@/lib/utils";

// Visual constants — fixed so the bar reads as one rhythm regardless
// of how many items are loaded.
const MAX_VISIBLE_SKILLS = 8;
const ROW_HEIGHT = "h-9";

function StatusDot({ openCircuits, t }: { openCircuits: number; t: ReturnType<typeof useI18n>["t"] }) {
  const { tone, label, Icon } = deriveStatusVisuals(openCircuits, t);
  return (
    <TooltipProvider delayDuration={150}>
      <Tooltip>
        <TooltipTrigger asChild>
          <button
            type="button"
            className={cn(
              "group inline-flex shrink-0 items-center gap-1.5 rounded-md px-2 py-1 text-xs font-medium transition-colors hover:bg-muted/60",
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

function deriveStatusVisuals(openCircuits: number, t: ReturnType<typeof useI18n>["t"]): {
  tone: { bg: string; dot: string; icon: string };
  label: { text: string; state: "healthy" | "degraded" | "critical"; tooltipTitle: string; tooltipBody: string };
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

function SkillPill({ skill }: { skill: CapabilitiesResponse["skills"][number] }) {
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
          <p className="text-muted-foreground/70 mt-1 text-[10px] uppercase tracking-wide">
            {skill.category} · {skill.enabled ? "enabled" : "disabled"}
          </p>
        </TooltipContent>
      </Tooltip>
    </TooltipProvider>
  );
}

function SkillRail({ skills, t }: { skills: CapabilitiesResponse["skills"]; t: ReturnType<typeof useI18n>["t"] }) {
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
              "inline-flex h-6 shrink-0 items-center gap-1 rounded-md border border-border/40 bg-background/50 px-1.5 font-mono text-[11px] transition-colors hover:bg-muted/60",
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
              "inline-flex h-6 shrink-0 items-center gap-1 rounded-md border border-border/40 bg-background/50 px-1.5 font-mono text-[11px] transition-colors hover:bg-muted/60",
            )}
            data-testid="runtime-igino-pill"
          >
            <ShieldIcon className="size-3 text-primary" aria-hidden />
            <span className="text-foreground/80">{t.runtimeBar.igino.label}</span>
            {status.tor_enabled && (
              <span className="bg-primary/20 text-primary rounded px-1 text-[9px]">{t.a11y.tor}</span>
            )}
          </span>
        </TooltipTrigger>
        <TooltipContent side="bottom" className="max-w-xs">
          <p className="font-medium">{t.runtimeBar.igino.title}</p>
          <p className="text-muted-foreground mt-1 text-xs">
            {t.runtimeBar.igino.tooltip(
              status.searxng_healthy ? t.agentComputer.privacy.healthy : t.agentComputer.privacy.unhealthy,
              status.tor_available ? t.agentComputer.privacy.available : t.agentComputer.privacy.unavailable,
              `${status.cache.size}/${status.cache.max_size}`,
            )}
          </p>
        </TooltipContent>
      </Tooltip>
    </TooltipProvider>
  );
}

export function RuntimeCapabilitiesBar({ className }: { className?: string }) {
  const { t } = useI18n();
  const { capabilities, isFetching, error } = useCapabilities();
  const openCircuits = useOpenCircuitCount();

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
          detail={t.runtimeBar.metrics.subagentsDetail}
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
                className="bg-destructive/10 text-destructive inline-flex h-6 shrink-0 items-center gap-1 rounded-md border border-destructive/30 px-1.5 font-mono text-[11px] font-medium"
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