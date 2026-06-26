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
  BoxesIcon,
  CheckCircle2Icon,
  CircuitBoardIcon,
  CogIcon,
  CpuIcon,
  LayersIcon,
  Loader2Icon,
  PlugZapIcon,
  SparklesIcon,
  TriangleAlertIcon,
  WrenchIcon,
  XCircleIcon,
} from "lucide-react";
import { useEffect, useState } from "react";

import { Badge } from "@/components/ui/badge";
import {
  Tooltip,
  TooltipContent,
  TooltipProvider,
  TooltipTrigger,
} from "@/components/ui/tooltip";
import { cn } from "@/lib/utils";

import { useCapabilities, useOpenCircuitCount } from "@/core/runtime/hooks";
import type { CapabilitiesResponse } from "@/core/runtime/types";

// Visual constants — fixed so the bar reads as one rhythm regardless
// of how many items are loaded.
const MAX_VISIBLE_SKILLS = 8;
const ROW_HEIGHT = "h-9";

function StatusDot({ openCircuits }: { openCircuits: number }) {
  const { tone, label, Icon } = deriveStatusVisuals(openCircuits);
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

function deriveStatusVisuals(openCircuits: number): {
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
        text: "Healthy",
        state: "healthy",
        tooltipTitle: "Browser subsystem healthy",
        tooltipBody: "All circuit breakers are CLOSED. No active degradation.",
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
        text: `Degraded · ${openCircuits}`,
        state: "degraded",
        tooltipTitle: `${openCircuits} circuit${openCircuits === 1 ? "" : "s"} open`,
        tooltipBody:
          "Some threads have hit the failure threshold. They will auto-recover after cooldown.",
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
      text: `Critical · ${openCircuits}`,
      state: "critical",
      tooltipTitle: `${openCircuits} circuits open — fleet degraded`,
      tooltipBody:
        "Multiple threads tripped. Check /api/health/browser for the full state.",
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

function SkillRail({ skills }: { skills: CapabilitiesResponse["skills"] }) {
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
              no skills loaded
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
                      +{overflow}
                    </span>
                  </TooltipTrigger>
                  <TooltipContent side="bottom" className="max-w-sm">
                    <p className="font-medium">
                      {overflow} more skill{overflow === 1 ? "" : "s"} loaded
                    </p>
                    <p className="text-muted-foreground mt-1 text-xs">
                      See Settings → Skills to manage.
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

export function RuntimeCapabilitiesBar({ className }: { className?: string }) {
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
        <span className="font-medium">Runtime status offline</span>
        <span className="text-muted-foreground/70">
          · will retry automatically
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
        aria-label="Agent runtime capabilities"
      >
        <StatusDot openCircuits={openCircuits} />

        <SectionDivider />

        <SkillRail skills={skills} />

        <SectionDivider />

        <MetricCounter
          icon={WrenchIcon}
          label="tools"
          count={tools.length}
          testId="runtime-tools-pill"
          detail="Builtin tools available to the lead agent."
        />
        <MetricCounter
          icon={CpuIcon}
          label="subagents"
          count={subagents.length}
          testId="runtime-subagents-pill"
          detail="Delegated worker agents the lead can spawn."
        />
        <MetricCounter
          icon={CogIcon}
          label="hooks"
          count={hooks.length}
          testId="runtime-hooks-pill"
          detail="Active middlewares on the LangChain agent chain."
        />

        {openCircuits > 0 && (
          <Tooltip>
            <TooltipTrigger asChild>
              <span
                className="bg-destructive/10 text-destructive inline-flex h-6 shrink-0 items-center gap-1 rounded-md border border-destructive/30 px-1.5 font-mono text-[11px] font-medium"
                data-testid="runtime-open-circuits-pill"
              >
                <PlugZapIcon className="size-3" aria-hidden />
                <span className="tabular-nums">{openCircuits}</span>
                <span>OPEN</span>
              </span>
            </TooltipTrigger>
            <TooltipContent side="bottom" className="max-w-sm">
              <p className="font-medium">
                {openCircuits} circuit{openCircuits === 1 ? "" : "s"} open
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
                Auto-recovers after cooldown.
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