"use client";

import { AnimatePresence, motion } from "motion/react";
import { useEffect, useRef, useState } from "react";

import { useI18n } from "@/core/i18n/hooks";
import { cn } from "@/lib/utils";

function getStatusLabel(
  tool: string | null,
  isLoading: boolean,
  filePath: string | null,
  lineCount: number | undefined,
  t: ReturnType<typeof useI18n>["t"],
): string {
  const filename = filePath?.split("/").at(-1);
  if (tool === "write_file") {
    return filename
      ? t.agentComputer.status.writing(
          filename,
          lineCount && lineCount > 1 ? `${lineCount} lines` : undefined,
        )
      : t.agentComputer.status.usingEditor;
  }
  if (tool === "str_replace")
    return filename
      ? t.agentComputer.status.editing(filename)
      : t.agentComputer.status.usingEditor;
  if (tool === "read_file")
    return filename
      ? t.agentComputer.status.reading(filename)
      : t.agentComputer.status.usingEditor;
  if (tool === "bash" || tool === "execute_command")
    return t.agentComputer.status.usingTerminal;
  if (tool === "search_files") return t.agentComputer.status.searchingFiles;
  if (tool === "grep_files") return t.agentComputer.status.searchingContent;
  if (tool === "task") return t.agentComputer.status.delegatingToSubagent;
  if (tool === "scaffold_project")
    return t.agentComputer.status.scaffoldingProject;
  if (tool === "browser" || tool === "web_search" || tool === "tavily_search")
    return t.agentComputer.status.usingBrowser;
  if (isLoading) return t.agentComputer.status.isThinking;
  return t.agentComputer.status.isIdle;
}

function getDotClass(tool: string | null, isLoading: boolean): string {
  if (tool === "bash" || tool === "execute_command") return "bg-emerald-400";
  if (tool === "write_file" || tool === "scaffold_project")
    return "bg-blue-400";
  if (tool === "str_replace") return "bg-purple-400";
  if (tool === "read_file") return "bg-sky-400";
  if (tool === "search_files" || tool === "grep_files") return "bg-orange-400";
  if (tool === "task") return "bg-indigo-400";
  if (isLoading) return "bg-yellow-400";
  return "bg-muted-foreground/40";
}

// ──────────────────────────────────────────────────────────
// Status line
// ──────────────────────────────────────────────────────────
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
  const label = getStatusLabel(tool, isLoading, filePath, lineCount, t);
  const dotClass = getDotClass(tool, isLoading);
  const pulse = isLoading || Boolean(tool);
  const startRef = useRef<number | null>(null);
  const [elapsed, setElapsed] = useState(0);

  useEffect(() => {
    if (pulse && startRef.current === null) {
      startRef.current = Date.now();
    } else if (!pulse) {
      startRef.current = null;
      setElapsed(0);
      return;
    }
    const id = setInterval(() => {
      if (startRef.current !== null) {
        setElapsed(Math.floor((Date.now() - startRef.current) / 1000));
      }
    }, 1000);
    return () => {
      clearInterval(id);
    };
  }, [pulse]);

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
            key={label}
            initial={{ opacity: 0, y: 4 }}
            animate={{ opacity: 1, y: 0 }}
            exit={{ opacity: 0, y: -4 }}
            transition={{ duration: 0.12 }}
            className="text-muted-foreground flex items-center gap-1 truncate text-xs"
          >
            <span className="truncate">{label}</span>
            {elapsed > 0 && (
              <span className="shrink-0 text-[10px] text-muted-foreground/60">
                · {elapsed}s
              </span>
            )}
          </motion.span>
        </AnimatePresence>
      </div>
    </div>
  );
}

