"use client";

import { useState } from "react";

import { useI18n } from "@/core/i18n/hooks";
import type { AgentActivityEvent } from "@/core/threads/hooks";
import { cn } from "@/lib/utils";

import { ActivityPanel } from "./activity-tab";
import { AuditPanel } from "./audit-tab";

/**
 * Activity and Audit, merged into one inspector.
 *
 * Both draw on the same underlying tool-call stream but answer different
 * questions, so this composes the two existing panels rather than rewriting
 * either. Two things had to survive the merge because they exist nowhere else
 * in the product: Audit's per-type filter and JSONL export, and Activity's live
 * workspace-intelligence pills. Keeping the components intact is what
 * guarantees that.
 *
 * **Timeline** — the curated feed: non-shell events as compact cards, plus the
 * verification banner and the live WIK scan/plan/cache pills.
 * **Ledger** — the complete, filterable, downloadable tool-call log.
 *
 * `active` is forwarded *per segment*, not just per tab. Every tab in this panel
 * stays mounted and is only hidden with CSS, so a panel that polls must be told
 * when it is off-screen or it keeps fetching forever (see frontend/CLAUDE.md).
 * A hidden segment is exactly as off-screen as a hidden tab, and `AuditPanel`
 * polls `/api/sandbox/audit` — so passing a bare `active` here would have
 * quietly reintroduced the bug that gating was added to fix.
 */
export function TelemetryPanel({
  threadId,
  events,
  verifyResult,
  active = true,
}: {
  threadId: string;
  events: AgentActivityEvent[];
  verifyResult?: React.ComponentProps<typeof ActivityPanel>["verifyResult"];
  active?: boolean;
}) {
  const { t } = useI18n();
  const [segment, setSegment] = useState<"timeline" | "ledger">("timeline");

  return (
    <div className="flex h-full flex-col">
      <div className="border-border/40 flex shrink-0 items-center gap-1 border-b px-2 py-1">
        <SegmentBtn
          active={segment === "timeline"}
          onClick={() => setSegment("timeline")}
        >
          {t.agentComputer.telemetry.timeline}
        </SegmentBtn>
        <SegmentBtn
          active={segment === "ledger"}
          onClick={() => setSegment("ledger")}
        >
          {t.agentComputer.telemetry.ledger}
        </SegmentBtn>
      </div>

      <div
        className={cn("min-h-0 flex-1", segment !== "timeline" && "hidden")}
        data-segment="timeline"
      >
        <ActivityPanel
          events={events}
          threadId={threadId}
          verifyResult={verifyResult}
          active={active && segment === "timeline"}
        />
      </div>

      <div
        className={cn("min-h-0 flex-1", segment !== "ledger" && "hidden")}
        data-segment="ledger"
      >
        <AuditPanel
          threadId={threadId}
          active={active && segment === "ledger"}
        />
      </div>
    </div>
  );
}

function SegmentBtn({
  active,
  onClick,
  children,
}: {
  active: boolean;
  onClick: () => void;
  children: React.ReactNode;
}) {
  return (
    <button
      onClick={onClick}
      className={cn(
        "rounded px-2 py-0.5 text-[11px] transition-colors",
        active
          ? "bg-muted text-foreground"
          : "text-muted-foreground/60 hover:text-muted-foreground",
      )}
    >
      {children}
    </button>
  );
}
