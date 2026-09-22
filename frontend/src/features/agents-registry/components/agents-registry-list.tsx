"use client";

import { useI18n } from "@/core/i18n/hooks";
import { cn } from "@/lib/utils";

import type { AgentKind, RegistryAgent } from "../api";
import { useAgentsRegistry } from "../hooks";

const KIND_ORDER: AgentKind[] = ["lead", "subagent", "custom", "acp"];

export function groupByKind(
  agents: RegistryAgent[],
): Array<{ kind: AgentKind; agents: RegistryAgent[] }> {
  return KIND_ORDER.map((kind) => ({
    kind,
    agents: agents.filter((a) => a.kind === kind),
  })).filter((g) => g.agents.length > 0);
}

/** Compact registry: who exists, where it runs, what it is doing right now. */
export function AgentsRegistryList({ compact = false }: { compact?: boolean }) {
  const { t } = useI18n();
  const s = t.features.agentsRegistry;
  const { data, isLoading, isError } = useAgentsRegistry();
  if (isLoading)
    return (
      <div
        className="bg-muted/40 h-16 animate-pulse rounded-lg"
        aria-busy="true"
      />
    );
  if (isError || !data)
    return (
      <p role="alert" className="text-destructive text-sm">
        {s.loadFailed}
      </p>
    );
  const active = data.agents.filter((a) => a.running > 0 || a.queued > 0);
  return (
    <div
      className="space-y-3"
      data-testid="agents-registry"
      data-async={data.async_enabled}
    >
      <p className="text-muted-foreground text-xs">
        {data.async_enabled ? s.asyncOn : s.asyncOff}
        {active.length > 0 &&
          ` · ${s.activeNow(
            active.reduce((n, a) => n + a.running, 0),
            active.reduce((n, a) => n + a.queued, 0),
          )}`}
      </p>
      {groupByKind(data.agents).map((group) => (
        <section key={group.kind}>
          <h3 className="text-muted-foreground mb-1 text-[11px] font-medium tracking-wide uppercase">
            {s.kinds[group.kind]}
          </h3>
          <ul
            className={cn(
              "divide-panel-border border-panel-border divide-y rounded-lg border",
              compact && "text-xs",
            )}
          >
            {group.agents.map((a) => (
              <li
                key={a.id}
                data-testid="registry-agent"
                data-kind={a.kind}
                className="flex items-center gap-3 px-3 py-2 text-sm"
              >
                <div className="min-w-0 flex-1">
                  <div className="flex items-center gap-2">
                    <span className="truncate font-medium">{a.name}</span>
                    {a.model && (
                      <span className="text-muted-foreground truncate font-mono text-[10px]">
                        {a.model}
                      </span>
                    )}
                  </div>
                  {!compact && a.description && (
                    <p className="text-muted-foreground truncate text-xs">
                      {a.description}
                    </p>
                  )}
                </div>
                <span className="text-muted-foreground shrink-0 font-mono text-[10px]">
                  {s.runner[a.runner]}
                </span>
                {(a.running > 0 || a.queued > 0) && (
                  <span
                    className="bg-info/15 text-info shrink-0 rounded-md px-2 py-0.5 font-mono text-[11px] tabular-nums"
                    data-testid="registry-counts"
                  >
                    {s.counts(a.running, a.queued)}
                  </span>
                )}
              </li>
            ))}
          </ul>
        </section>
      ))}
    </div>
  );
}
