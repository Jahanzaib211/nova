"use client";

import {
  CheckCircle2Icon,
  CircleIcon,
  DownloadIcon,
  FileSearchIcon,
  FileTextIcon,
  FolderOpenIcon,
  AlertTriangleIcon,
  GlobeIcon,
  LoaderCircleIcon,
  PencilIcon,
  SquareTerminalIcon,
  TerminalIcon,
  DatabaseIcon,
} from "lucide-react";
import { motion } from "motion/react";
import { useEffect, useMemo, useRef } from "react";

import { Progress } from "@/components/ui/progress";
import { ScrollArea } from "@/components/ui/scroll-area";
import type {
  LlmError,
  TaskProgress,
  VerifyResult,
} from "@/components/workspace/messages/context";
import { useI18n } from "@/core/i18n/hooks";
import { sandboxAuditDownloadUrl } from "@/core/sandbox/hooks";
import type { AgentActivityEvent } from "@/core/threads/hooks";
import { classifyToolWork, isActivityTool } from "@/core/threads/tool-surface";
import type { Todo } from "@/core/todos";
import {
  useWorkspaceEvents,
  useWorkspaceSnapshot,
} from "@/core/workspace/hooks";
import { cn } from "@/lib/utils";

// Tab 4: Activity — compact event cards + Files tree
// ──────────────────────────────────────────────────────────
// Icon/color per work kind, from the shared classifier. The previous private
 // tool-name list drifted silently — unknown tools fell to the default icon,
// which is invisible in the UI and was exactly the pre-2026-08-21 failure
// shape (see core/threads/tool-surface.ts).
function getToolMeta(type: string): { icon: React.ReactNode; color: string } {
  switch (classifyToolWork(type)) {
    case "file-write":
      return {
        icon: <PencilIcon className="h-3 w-3" />,
        color: "text-blue-400",
      };
    case "file-edit":
      return {
        icon: <PencilIcon className="h-3 w-3" />,
        color: "text-purple-400",
      };
    case "file-read":
      return {
        icon: <FileTextIcon className="h-3 w-3" />,
        color: "text-sky-400",
      };
    case "file-search":
    case "content-search":
      return {
        icon: <FileSearchIcon className="h-3 w-3" />,
        color: "text-orange-400",
      };
    case "scaffold":
      return {
        icon: <FolderOpenIcon className="h-3 w-3" />,
        color: "text-indigo-400",
      };
    case "subagent":
      return {
        icon: <SquareTerminalIcon className="h-3 w-3" />,
        color: "text-indigo-400",
      };
    case "browser":
      return {
        icon: <GlobeIcon className="h-3 w-3" />,
        color: "text-cyan-400",
      };
    default:
      return {
        icon: <TerminalIcon className="h-3 w-3" />,
        color: "text-emerald-400",
      };
  }
}

function ActivityEventCard({
  event,
  index,
}: {
  event: AgentActivityEvent;
  index?: number;
}) {
  const meta = getToolMeta(event.type);
  const filename = event.path?.split("/").at(-1);
  const isRunning = event.status === "running";
  const isError = event.status === "error";
  return (
    <motion.div
      initial={{ opacity: 0, y: -6 }}
      animate={{ opacity: 1, y: 0 }}
      transition={{
        duration: 0.25,
        ease: "easeOut",
        delay: typeof index === "number" ? Math.min(index, 8) * 0.04 : 0,
      }}
      className={cn(
        "rounded border px-2 py-1.5 text-xs",
        isError
          ? "border-red-500/20 bg-red-500/5"
          : "border-border/20 bg-muted/10",
      )}
    >
      <div className="flex items-center gap-1.5">
        <span className={cn("shrink-0", meta.color)}>{meta.icon}</span>
        <span className={cn("font-mono text-[11px] font-medium", meta.color)}>
          {event.type}
        </span>
        {filename && (
          <span className="text-muted-foreground/60 truncate text-[10px]">
            {filename}
          </span>
        )}
        {/* Message-derived events carry no wall-clock ts; render nothing
            rather than an empty right-aligned slot. */}
        {event.ts && (
          <span className="text-muted-foreground/40 ml-auto shrink-0 text-[10px]">
            {event.ts}
          </span>
        )}
        {isRunning && (
          <LoaderCircleIcon className="text-muted-foreground/40 h-2.5 w-2.5 animate-spin" />
        )}
      </div>
      {event.summary && (
        <div className="text-muted-foreground/50 mt-0.5 truncate pl-5 text-[10px]">
          {event.summary}
        </div>
      )}
    </motion.div>
  );
}

// Tab: Activity — deduped high-level action timeline + export (absorbs Audit).
// Excludes raw bash/search/grep (the Terminal tab owns those) so there's no
// Terminal/Activity duplication.
// ──────────────────────────────────────────────────────────

// Compact pill that surfaces the most recent deterministic verify_result
// at the top of the Activity tab. Sourced from the verify_result custom
// event emitted by the backend's auto-verify-on-present_files gate.

/** Known LLM-error reasons → localized cause text. */
const LLM_ERROR_REASONS: Record<string, (t: ReturnType<typeof useI18n>["t"]) => string> = {
  quota: (t) => t.agentComputer.llmError.quota,
  auth: (t) => t.agentComputer.llmError.auth,
  busy: (t) => t.agentComputer.llmError.busy,
  transient: (t) => t.agentComputer.llmError.busy,
  circuit_open: (t) => t.agentComputer.llmError.busy,
} as const;

export function LlmErrorBadge({ event }: { event: LlmError }) {
  const { t } = useI18n();
  const reason = (event.reason || "unknown").toLowerCase();
  const tone =
    reason === "quota"
      ? "border-amber-500/40 bg-amber-500/10 text-amber-700 dark:text-amber-300"
      : reason === "auth"
        ? "border-red-500/40 bg-red-500/10 text-red-700 dark:text-red-300"
        : "border-orange-500/40 bg-orange-500/10 text-orange-700 dark:text-orange-300";
  // i18n: these were hardcoded English; the reasons map keeps known causes
  // specific and everything else falls to a generic line.
  const reasonFn = LLM_ERROR_REASONS[reason];
  const reasonText = reasonFn
    ? reasonFn(t)
    : t.agentComputer.llmError.generic;
  const label = `${t.agentComputer.llmError.prefix}: ${reasonText}`;
  return (
    <div
      role="status"
      aria-live="polite"
      className={cn(
        "border-border/30 flex shrink-0 items-center gap-2 border-b px-3 py-2 font-mono text-xs",
        tone,
      )}
      data-testid="llm-error-badge"
      title={event.detail || event.error_type || ""}
    >
      <AlertTriangleIcon className="h-3.5 w-3.5" aria-hidden />
      <span className="truncate">{label}</span>
      <span className="text-muted-foreground/70 ml-auto">
        {event.error_type}
      </span>
    </div>
  );
}

function VerifyResultPill({ event }: { event: VerifyResult }) {
  const { t } = useI18n();
  const ok = event.ok;
  const failedRoutes = (event.routes ?? []).filter((r) => !r.ok);
  const summary = ok
    ? t.agentComputer.verifyResult.passed(event.routes?.length ?? 0)
    : t.agentComputer.verifyResult.failed(failedRoutes.length);
  const tone = ok
    ? "border-emerald-500/40 bg-emerald-500/10 text-emerald-700 dark:text-emerald-300"
    : "border-amber-500/40 bg-amber-500/10 text-amber-700 dark:text-amber-300";
  return (
    <div
      className={cn(
        "border-border/30 flex shrink-0 items-center gap-2 border-b px-3 py-2 font-mono text-xs",
        tone,
      )}
      data-testid="verify-result-pill"
    >
      <span aria-hidden>{ok ? "✓" : "✗"}</span>
      <span className="truncate">{summary}</span>
      {event.console_errors_count > 0 ? (
        <span className="text-muted-foreground/70 ml-auto">
          {t.agentComputer.verifyResult.consoleErrors(
            event.console_errors_count,
          )}
        </span>
      ) : null}
    </div>
  );
}

export function ActivityPanel({
  events,
  threadId,
  verifyResult,
  active = true,
}: {
  events: AgentActivityEvent[];
  threadId: string;
  verifyResult?: VerifyResult | null;
  /** Gates the snapshot query and SSE subscription so they don't run while
   * the tab is hidden but stays mounted (the SSE connection in particular
   * would otherwise leak — EventSource has no automatic idle-close). */
  active?: boolean;
}) {
  const { t } = useI18n();
  const bottomRef = useRef<HTMLDivElement>(null);
  const timeline = useMemo(
    () => events.filter((e) => isActivityTool(e.type)),
    [events],
  );
  // Workspace-indexed banner (C10 item 7); null while the flag is off.
  const { snapshot } = useWorkspaceSnapshot(threadId, active);
  // Live WIK activity — bus -> SSE bridge; empty while the flag is off.
  const liveEvents = useWorkspaceEvents(threadId, active);
  const recentLiveEvents = useMemo(() => liveEvents.slice(-5), [liveEvents]);

  // Follow the feed. Depending on `length` alone stopped working on exactly the
  // runs where following matters most: the event buffer is capped at 200 and
  // trims from the front, so past that point every new event drops an old one,
  // `length` is pinned at the cap, and the effect never fired again — the feed
  // silently stopped scrolling partway through any long run. Keying on the
  // newest event as well keeps it firing once the cap is reached.
  //
  // Gated on `active` (terminal-tab.tsx:93 is the pattern): every tab stays
  // mounted via CSS `hidden`, so an ungated scroll drags a hidden subtree on
  // every event — and because `active` was absent from deps, returning to the
  // tab after events piled up opened it mid-scroll instead of at the bottom.
  const newestEvent = timeline.at(-1);
  const newestId = newestEvent?.id;
  const newestStatus = newestEvent?.status;
  useEffect(() => {
    if (!active) return;
    bottomRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [active, timeline.length, newestId, newestStatus]);

  return (
    <div className="flex h-full flex-col">
      {verifyResult ? <VerifyResultPill event={verifyResult} /> : null}
      <div className="border-border/30 bg-muted/20 flex shrink-0 items-center justify-between border-b px-2 py-1">
        <span className="text-muted-foreground/70 font-mono text-xs">
          {t.agentComputer.activity.title(timeline.length)}
        </span>
        <a
          href={sandboxAuditDownloadUrl(threadId)}
          download
          className="text-muted-foreground/60 hover:text-foreground rounded p-1 transition-colors"
          title={t.agentComputer.activity.exportAuditLog}
        >
          <DownloadIcon className="h-3 w-3" />
        </a>
      </div>
      <ScrollArea className="min-h-0 flex-1">
        <div className="flex flex-col gap-1 p-2">
          {snapshot && (
            <div className="border-border/30 bg-muted/10 text-muted-foreground/70 flex items-center gap-1.5 rounded border px-2 py-1 text-[10px]">
              <DatabaseIcon className="h-3 w-3 shrink-0 text-emerald-400" />
              <span className="truncate">
                {t.agentComputer.workspace.indexedBanner(
                  snapshot.symbol_count,
                  snapshot.project_count,
                  snapshot.command_count,
                  snapshot.primary_language,
                )}
              </span>
            </div>
          )}
          {recentLiveEvents.map((event, i) => {
            // Live frames are blind-cast from parsed SSE; a malformed body
            // must render as a skipped pill, not crash the tab.
            const data = (event.data ?? {}) as unknown as Record<
              string,
              unknown
            >;
            const num = (k: string): number =>
              typeof data[k] === "number" ? data[k] : 0;
            return (
              <div
                key={`${event.type}-${String(data.occurred_at)}-${i}`}
                className="border-border/20 bg-muted/5 text-muted-foreground/60 flex items-center gap-1.5 rounded border px-2 py-1 text-[10px]"
              >
                <DatabaseIcon className="h-3 w-3 shrink-0 text-sky-400" />
                <span className="truncate">
                  {event.type === "WorkspaceScanned"
                    ? t.agentComputer.workspace.liveScanned(
                        num("symbol_count"),
                        num("scan_duration_ms"),
                      )
                    : event.type === "PlanBuilt"
                      ? t.agentComputer.workspace.livePlan(
                          num("step_count"),
                          typeof data.risk_level === "string"
                            ? data.risk_level
                            : "?",
                        )
                      : event.type === "CacheHit"
                        ? t.agentComputer.workspace.liveCacheHit
                        : t.agentComputer.workspace.liveCacheMiss}
                </span>
              </div>
            );
          })}
          {timeline.length === 0 ? (
            <div className="flex flex-col items-center justify-center gap-2 py-6 text-center">
              <FileTextIcon className="text-muted-foreground/30 h-5 w-5" />
              <span className="text-muted-foreground/50 text-xs">
                {t.agentComputer.activity.empty}
              </span>
            </div>
          ) : (
            timeline.map((event, i) => (
              <ActivityEventCard key={event.id || i} index={i} event={event} />
            ))
          )}
          <div ref={bottomRef} />
        </div>
      </ScrollArea>
    </div>
  );
}

// ──────────────────────────────────────────────────────────
// Task checklist
// ──────────────────────────────────────────────────────────
export function TaskChecklist({
  todos,
  taskProgress,
  activityEvents,
}: {
  todos: Todo[];
  taskProgress: TaskProgress | null;
  activityEvents: AgentActivityEvent[];
}) {
  const { t } = useI18n();
  if (todos.length === 0) return null;

  // Enrich todo status from activity events (subagent task completions)
  const completedTaskCount = activityEvents.filter(
    (e) => e.type === "task" && e.status === "done",
  ).length;
  const enrichedTodos = todos.map((todo, i) =>
    i < completedTaskCount ? { ...todo, status: "completed" as const } : todo,
  );

  const done = enrichedTodos.filter((t) => t.status === "completed").length;
  const total = enrichedTodos.length;
  const step = taskProgress?.step ?? done;
  const totalSteps = taskProgress?.total ?? total;
  const pct = totalSteps > 0 ? (step / totalSteps) * 100 : 0;

  return (
    <div className="flex flex-col gap-1 px-3 py-2">
      <div className="flex items-center justify-between">
        <span className="text-muted-foreground/70 text-xs font-medium">
          {t.agentComputer.taskProgress}
        </span>
        <span className="text-muted-foreground/50 text-xs">
          {step} / {totalSteps}
        </span>
      </div>
      <Progress value={pct} className="h-1" />
      <div className="mt-0.5 flex flex-col gap-0.5">
        {enrichedTodos.map((todo, i) => {
          const isCompleted = todo.status === "completed";
          const isInProgress = todo.status === "in_progress";
          return (
            <div
              // Index-prefixed so the key is unique even when two todos share
              // the same text, or when content is absent entirely (it is
              // `content?: string`, so both undefined and "" are possible).
              // A bare `todo.content` collided on duplicates; a bare `??`
              // would make every empty-content todo share the key "".
              key={`${i}:${todo.content ?? ""}`}
              className={cn(
                "flex items-start gap-1.5 rounded px-1 py-0.5 text-xs",
                isInProgress && "bg-muted/40",
              )}
            >
              {isCompleted ? (
                <CheckCircle2Icon className="mt-px h-3 w-3 shrink-0 text-emerald-500" />
              ) : isInProgress ? (
                <LoaderCircleIcon className="text-primary/70 mt-px h-3 w-3 shrink-0 animate-spin" />
              ) : (
                <CircleIcon className="text-muted-foreground/30 mt-px h-3 w-3 shrink-0" />
              )}
              <span
                className={cn(
                  "text-[11px] leading-snug",
                  isCompleted
                    ? "text-muted-foreground/40 line-through"
                    : isInProgress
                      ? "text-primary/70"
                      : "text-muted-foreground/70",
                )}
              >
                {todo.content}
              </span>
            </div>
          );
        })}
      </div>
    </div>
  );
}
