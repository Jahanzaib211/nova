"use client";
import { AnimatePresence, motion } from "motion/react";
import { useCallback, useEffect, useRef, useState } from "react";

import { useI18n } from "@/core/i18n/hooks";
import {
  classifyToolWork,
  type ToolWorkKind,
} from "@/core/threads/tool-surface";
import { cn } from "@/lib/utils";

/**
 * Status label + dot color, derived from the shared work-kind classifier
 * (core/threads/tool-surface.ts) instead of a private tool-name list. The
 * list form drifted: the whole `shell_*` family fell through to "Thinking…"
 * with a yellow dot while the agent was doing real terminal work.
 *
 * Kind → meaning mapping lives here because it is presentation; the
 * classification itself lives in one place shared with the tab partition,
 * auto-switch, and activity icons.
 */
function getStatusLabel(
  kind: ToolWorkKind,
  isLoading: boolean,
  filePath: string | null,
  lineCount: number | undefined,
  t: ReturnType<typeof useI18n>["t"],
): string {
  const filename = filePath?.split("/").at(-1);
  switch (kind) {
    case "file-write":
      return filename
        ? t.agentComputer.status.writing(
            filename,
            lineCount && lineCount > 1 ? `${lineCount} lines` : undefined,
          )
        : t.agentComputer.status.usingEditor;
    case "file-edit":
      return filename
        ? t.agentComputer.status.editing(filename)
        : t.agentComputer.status.usingEditor;
    case "file-read":
      return filename
        ? t.agentComputer.status.reading(filename)
        : t.agentComputer.status.usingEditor;
    case "terminal":
      return t.agentComputer.status.usingTerminal;
    case "file-search":
      return t.agentComputer.status.searchingFiles;
    case "content-search":
      return t.agentComputer.status.searchingContent;
    case "subagent":
      return t.agentComputer.status.delegatingToSubagent;
    case "scaffold":
      return t.agentComputer.status.scaffoldingProject;
    case "browser":
      // `web_search`/`screenshot` are web work too; anything unrecognized that
      // mentions search keeps this label rather than reading as thinking.
      return t.agentComputer.status.usingBrowser;
    default:
      if (isLoading) return t.agentComputer.status.isThinking;
      return t.agentComputer.status.isIdle;
  }
}

const DOT_CLASS_BY_KIND: Partial<Record<ToolWorkKind, string>> = {
  terminal: "bg-emerald-400",
  "file-write": "bg-blue-400",
  "file-edit": "bg-purple-400",
  "file-read": "bg-sky-400",
  "file-search": "bg-orange-400",
  "content-search": "bg-orange-400",
  subagent: "bg-indigo-400",
  scaffold: "bg-indigo-400",
  browser: "bg-cyan-400",
  devserver: "bg-violet-400",
};

function getDotClass(kind: ToolWorkKind, isLoading: boolean): string {
  return (
    DOT_CLASS_BY_KIND[kind] ??
    (isLoading ? "bg-yellow-400" : "bg-muted-foreground/40")
  );
}

// ──────────────────────────────────────────────────────────
// Status line
// ──────────────────────────────────────────────────────────

/**
 * Between two real tools the stream briefly reports "no tool", which used to
 * flip the label to "Thinking…" and back — a flicker on every tool boundary.
 * A gap shorter than this is treated as the same tool still being reported.
 */
const TOOL_GAP_HOLD_MS = 400;

export function StatusLine({
  tool,
  isLoading,
  filePath,
  lineCount,
}: {
  tool: string | null;
  isLoading: boolean;
  filePath: string | null;
  lineCount?: number;
}) {
  const { t } = useI18n();
  const kind = classifyToolWork(tool ?? "");
  const thinkingLabel = t.agentComputer.status.isThinking;
  const label = getStatusLabel(kind, isLoading, filePath, lineCount, t);
  const dotClass = getDotClass(kind, isLoading);
  const pulse = isLoading || Boolean(tool);

  // Hold the last real tool label through short "no tool" gaps so the label
  // does not flicker through Thinking between commands.
  const shownRef = useRef(label);
  const [, forceRender] = useState(0);
  const gapTimerRef = useRef<ReturnType<typeof setTimeout> | undefined>(
    undefined,
  );

  const present = useCallback((next: string) => {
    if (next !== shownRef.current) {
      shownRef.current = next;
      forceRender((n) => n + 1);
    }
  }, []);

  useEffect(() => {
    // Only a literal Thinking label is a gap candidate — an unknown tool's
    // own label must appear immediately, never be suppressed.
    if (label !== thinkingLabel) {
      clearTimeout(gapTimerRef.current);
      gapTimerRef.current = undefined;
      present(label);
      return;
    }
    // Thinking: adopt it only if it survives longer than the gap window;
    // otherwise keep showing the previous tool's label.
    gapTimerRef.current ??= setTimeout(() => {
      gapTimerRef.current = undefined;
      present(label);
    }, TOOL_GAP_HOLD_MS);
  }, [label, present, thinkingLabel]);

  useEffect(() => () => clearTimeout(gapTimerRef.current), []);

  const displayedLabel = shownRef.current;

  // Elapsed time restarts with each SHOWN tool — it used to run per-run, so a
  // status read "Using terminal · 480s" seconds into a command because the
  // clock had been ticking since the run's first tool.
  const [elapsed, setElapsed] = useState(0);
  useEffect(() => {
    if (!pulse) {
      setElapsed(0);
      return;
    }
    setElapsed(0);
    const start = Date.now();
    const id = setInterval(() => {
      setElapsed(Math.floor((Date.now() - start) / 1000));
    }, 1000);
    return () => clearInterval(id);
  }, [pulse, displayedLabel]);

  return (
    <div className="border-border/50 flex items-center gap-2 border-b px-3 py-1.5">
      <span className="relative inline-flex h-2 w-2 shrink-0">
        {pulse && (
          <span
            className={cn(
              "absolute inset-0 -m-1 rounded-full opacity-40 motion-safe:animate-ping",
              dotClass,
            )}
            aria-hidden="true"
          />
        )}
        <span
          className={cn(
            "relative inline-block h-2 w-2 shrink-0 rounded-full transition-colors duration-300",
            dotClass,
            pulse && "animate-pulse",
          )}
        />
      </span>
      <div className="relative min-w-0 flex-1 overflow-hidden">
        <AnimatePresence mode="wait">
          <motion.span
            key={displayedLabel}
            initial={{ opacity: 0, y: 4 }}
            animate={{ opacity: 1, y: 0 }}
            exit={{ opacity: 0, y: -4 }}
            transition={{ duration: 0.12 }}
            className="text-muted-foreground flex items-center gap-1 truncate text-xs"
          >
            <span className="truncate">{displayedLabel}</span>
            {elapsed > 0 && (
              <span className="text-muted-foreground/60 shrink-0 text-[10px]">
                · {elapsed}s
              </span>
            )}
          </motion.span>
        </AnimatePresence>
      </div>
    </div>
  );
}
