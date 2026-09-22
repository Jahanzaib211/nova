"use client";

import {
  DownloadIcon,
  FilterIcon,
  ScrollTextIcon,
  TerminalIcon,
} from "lucide-react";
import { useMemo, useState } from "react";

import { Button } from "@/components/ui/button";
import { ScrollArea } from "@/components/ui/scroll-area";
import { useI18n } from "@/core/i18n/hooks";
import {
  sandboxAuditDownloadUrl,
  useSandboxAudit,
  type AuditEvent,
} from "@/core/sandbox/hooks";
import { cn } from "@/lib/utils";

const EVENT_TYPE_ICONS: Record<string, typeof TerminalIcon> = {
  bash: TerminalIcon,
  execute_command: TerminalIcon,
  write_file: ScrollTextIcon,
  read_file: ScrollTextIcon,
  str_replace: ScrollTextIcon,
  glob: FilterIcon,
  grep: FilterIcon,
};

const EVENT_TYPE_COLORS: Record<string, string> = {
  bash: "text-success bg-success/10",
  execute_command: "text-success bg-success/10",
  write_file: "text-info bg-info/10",
  read_file: "text-info bg-info/10",
  str_replace: "text-warning bg-warning/10",
  glob: "text-info bg-info/10",
  grep: "text-info bg-info/10",
};

const FILTER_TYPES = [
  "all",
  "bash",
  "write_file",
  "read_file",
  "str_replace",
] as const;

type FilterType = (typeof FILTER_TYPES)[number];

function formatTimestamp(ts?: string): string {
  if (!ts) return "";
  try {
    const d = new Date(ts);
    return d.toLocaleTimeString([], {
      hour: "2-digit",
      minute: "2-digit",
      second: "2-digit",
    });
  } catch {
    return ts;
  }
}

function AuditEventRow({ event }: { event: AuditEvent }) {
  const Icon = EVENT_TYPE_ICONS[event.type ?? ""] ?? ScrollTextIcon;
  const colorClass =
    EVENT_TYPE_COLORS[event.type ?? ""] ?? "text-muted-foreground bg-muted/50";

  return (
    <div className="border-panel-border flex items-start gap-2 border-b px-3 py-2 last:border-b-0">
      <span
        className={cn(
          "mt-0.5 flex h-5 w-5 shrink-0 items-center justify-center rounded",
          colorClass,
        )}
      >
        <Icon className="h-3 w-3" />
      </span>
      <div className="min-w-0 flex-1">
        <div className="flex items-center gap-2">
          <span className="text-foreground text-xs font-medium">
            {event.type ?? "event"}
          </span>
          {event.path && (
            <span className="text-muted-foreground truncate text-[10px]">
              {event.path}
            </span>
          )}
          <span className="text-muted-foreground/50 ml-auto shrink-0 text-[10px]">
            {formatTimestamp(event.ts)}
          </span>
        </div>
        {event.summary && (
          <p className="text-muted-foreground mt-0.5 text-[11px] leading-snug">
            {event.summary}
          </p>
        )}
        {event.output && (
          <pre className="bg-muted/30 mt-1 max-h-20 overflow-auto rounded p-1.5 text-[10px] leading-relaxed">
            {event.output.length > 500
              ? `${event.output.slice(0, 500)}…`
              : event.output}
          </pre>
        )}
      </div>
    </div>
  );
}

export function AuditPanel({
  threadId,
  active = true,
}: {
  threadId: string;
  active?: boolean;
}) {
  const { t } = useI18n();
  const events = useSandboxAudit(threadId, active);
  const [filter, setFilter] = useState<FilterType>("all");

  const filtered = useMemo(() => {
    if (filter === "all") return events;
    return events.filter((e) => e.type === filter);
  }, [events, filter]);

  const downloadUrl = sandboxAuditDownloadUrl(threadId);

  return (
    <div className="flex h-full flex-col">
      {/* Toolbar */}
      <div className="border-panel-border flex shrink-0 items-center gap-2 border-b px-3 py-1.5">
        <ScrollTextIcon className="text-muted-foreground h-3 w-3" />
        <span className="text-muted-foreground text-[11px] font-medium">
          {t.agentComputer.tabs.audit}
        </span>
        <div className="ml-auto flex items-center gap-1">
          {FILTER_TYPES.map((ft) => (
            <button
              key={ft}
              onClick={() => setFilter(ft)}
              className={cn(
                "rounded px-1.5 py-0.5 text-[10px] font-medium transition-colors",
                filter === ft
                  ? "bg-primary/10 text-foreground"
                  : "text-muted-foreground/60 hover:text-muted-foreground",
              )}
            >
              {ft === "all" ? "All" : ft.replace("_", " ")}
            </button>
          ))}
          <a
            href={downloadUrl}
            download
            className="text-muted-foreground/60 hover:text-muted-foreground ml-1"
          >
            <Button size="icon-sm" variant="ghost" className="h-6 w-6">
              <DownloadIcon className="h-3 w-3" />
            </Button>
          </a>
        </div>
      </div>

      {/* Event list */}
      {filtered.length === 0 ? (
        <div className="text-muted-foreground flex h-full items-center justify-center p-4 text-sm">
          {events.length === 0
            ? "No audit events yet"
            : "No events match this filter"}
        </div>
      ) : (
        <ScrollArea className="min-h-0 flex-1">
          <div className="divide-none">
            {filtered.map((event, i) => (
              <AuditEventRow key={`${event.ts}-${i}`} event={event} />
            ))}
          </div>
        </ScrollArea>
      )}
    </div>
  );
}
