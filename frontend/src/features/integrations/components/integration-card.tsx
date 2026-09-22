"use client";

import { RefreshCwIcon } from "lucide-react";

import { Button } from "@/components/ui/button";
import { useI18n } from "@/core/i18n/hooks";
import type { Translations } from "@/core/i18n/locales/types";
import { cn } from "@/lib/utils";

import { DOT_CLASS, TEXT_CLASS, toneFor } from "../status";
import type { IntegrationHealthItem } from "../types";

export function relativeCheckedAt(
  iso: string | null | undefined,
  s: Translations["features"]["integrations"],
  now: number = Date.now(),
): string {
  if (!iso) return s.neverChecked;
  const ms = now - new Date(iso).getTime();
  if (!Number.isFinite(ms) || ms < 5_000) return s.justNow;
  const sec = Math.round(ms / 1000);
  if (sec < 60) return s.secondsAgo(sec);
  return s.minutesAgo(Math.round(sec / 60));
}

export function IntegrationCard({
  item,
  probing,
  onProbe,
}: {
  item: IntegrationHealthItem;
  probing?: boolean;
  onProbe?: (id: string) => void;
}) {
  const { t } = useI18n();
  const s = t.features.integrations;
  const tone = toneFor(item.status);
  const caps = item.capabilities ?? [];
  const shown = caps.slice(0, 6);
  return (
    <article
      data-testid="integration-card"
      data-status={item.status}
      data-kind={item.kind}
      className="border-panel-border bg-panel flex flex-col gap-3 rounded-lg border p-4"
    >
      <header className="flex items-start justify-between gap-3">
        <div className="min-w-0">
          <h4 className="truncate text-sm font-semibold">
            {item.display_name}
          </h4>
          {item.endpoint && (
            <p className="text-muted-foreground truncate font-mono text-[11px]">
              {item.endpoint}
            </p>
          )}
        </div>
        <span
          className={cn(
            "inline-flex shrink-0 items-center gap-1.5 text-xs font-medium",
            TEXT_CLASS[tone],
          )}
        >
          <span
            className={cn("size-2 rounded-full", DOT_CLASS[tone])}
            aria-hidden
          />
          {s.status[item.status]}
        </span>
      </header>
      {item.detail && (
        <p className="text-muted-foreground text-xs leading-snug">
          {item.detail}
        </p>
      )}
      {shown.length > 0 && (
        <ul className="flex flex-wrap gap-1" aria-label={s.capabilities}>
          {shown.map((cap) => (
            <li
              key={cap}
              className="bg-muted text-muted-foreground rounded px-1.5 py-0.5 font-mono text-[10px]"
            >
              {cap}
            </li>
          ))}
          {caps.length > shown.length && (
            <li className="text-muted-foreground px-1 text-[10px]">
              +{caps.length - shown.length}
            </li>
          )}
        </ul>
      )}
      <footer className="text-muted-foreground mt-auto flex items-center justify-between gap-2 text-[11px] tabular-nums">
        <span>
          {item.latency_ms != null && `${Math.round(item.latency_ms)} ms · `}
          <time dateTime={item.checked_at ?? undefined}>
            {relativeCheckedAt(item.checked_at, s)}
          </time>
        </span>
        {onProbe && item.endpoint && (
          <Button
            variant="ghost"
            size="sm"
            className="h-7 px-2"
            disabled={probing}
            onClick={() => onProbe(item.id)}
            aria-label={`${s.probe} ${item.display_name}`}
          >
            <RefreshCwIcon
              className={cn("size-3.5", probing && "animate-spin")}
            />
            {s.probe}
          </Button>
        )}
      </footer>
    </article>
  );
}
