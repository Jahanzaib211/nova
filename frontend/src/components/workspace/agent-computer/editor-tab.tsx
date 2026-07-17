"use client";

import { CodeIcon, LoaderCircleIcon, PencilIcon } from "lucide-react";
import { useEffect, useMemo, useRef, useState } from "react";

import { useI18n } from "@/core/i18n/hooks";
import { useLiveFileContent } from "@/core/sandbox/hooks";
import { diffStats, lineDiff, type DiffLine } from "@/lib/line-diff";
import { cn } from "@/lib/utils";

import type { ActiveEdit } from "./message-helpers";

// Tab 2: Editor — live code view as agent writes
// ──────────────────────────────────────────────────────────
function getCodeColor(filename: string): string {
  const ext = filename.split(".").at(-1)?.toLowerCase() ?? "";
  if (ext === "html" || ext === "htm") return "text-orange-300";
  if (ext === "css" || ext === "scss") return "text-pink-300";
  if (ext === "js" || ext === "jsx" || ext === "mjs") return "text-yellow-300";
  if (ext === "ts" || ext === "tsx") return "text-blue-300";
  if (ext === "json") return "text-green-300";
  if (ext === "md") return "text-sky-300";
  if (ext === "py") return "text-emerald-300";
  return "text-slate-300";
}

// Renders an interleaved red/green line diff (zai/cursor style).
function DiffView({ lines }: { lines: DiffLine[] }) {
  return (
    <pre className="min-w-full p-0 font-mono text-[11px] leading-relaxed">
      {lines.map((l, i) => (
        <div
          key={i}
          className={cn(
            "flex px-2 break-all whitespace-pre-wrap",
            l.type === "add" && "bg-emerald-500/10 text-emerald-300",
            l.type === "del" && "bg-red-500/10 text-red-300/90",
            l.type === "ctx" && "text-muted-foreground/70",
          )}
        >
          <span className="mr-2 inline-block w-3 shrink-0 text-center opacity-60 select-none">
            {l.type === "add" ? "+" : l.type === "del" ? "−" : " "}
          </span>
          <span className="min-w-0 flex-1">{l.text || " "}</span>
        </div>
      ))}
    </pre>
  );
}

export function Editor({
  threadId,
  filePath,
  isWriting,
  activeTab,
  activeEdit,
}: {
  threadId: string;
  filePath: string | null;
  isWriting: boolean;
  activeTab: boolean;
  activeEdit: ActiveEdit | null;
}) {
  const { t } = useI18n();
  const { content, exists, lineCount } = useLiveFileContent(
    threadId,
    filePath,
    activeTab && Boolean(filePath),
  );
  const bottomRef = useRef<HTMLDivElement>(null);
  const filename = filePath?.split("/").at(-1) ?? "";
  const codeColor = getCodeColor(filename);

  // The diff is only meaningful for the file currently shown.
  const editForFile = activeEdit?.path === filePath ? activeEdit : null;
  const diff = useMemo<DiffLine[] | null>(() => {
    if (!editForFile) return null;
    return editForFile.kind === "str_replace"
      ? lineDiff(editForFile.oldStr, editForFile.newStr)
      : lineDiff("", editForFile.content);
  }, [editForFile]);
  const stats = useMemo(() => (diff ? diffStats(diff) : null), [diff]);

  // Default to Diff while an edit is fresh; let the user flip to the full file.
  const [mode, setMode] = useState<"diff" | "file">("diff");
  useEffect(() => {
    if (editForFile) setMode("diff");
  }, [editForFile]);
  const showDiff = mode === "diff" && diff !== null;

  // Auto-scroll while writing
  useEffect(() => {
    if (isWriting) bottomRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [content, isWriting, showDiff]);

  if (!filePath) {
    return (
      <div className="flex h-full flex-col items-center justify-center gap-2 text-center">
        <PencilIcon className="text-muted-foreground/30 h-6 w-6" />
        <span className="text-muted-foreground/50 text-xs">
          {t.agentComputer.editor.startWriting}
        </span>
      </div>
    );
  }

  return (
    <div className="flex h-full flex-col bg-black/50">
      {/* File header */}
      <div className="border-border/30 flex shrink-0 items-center gap-2 border-b bg-black/40 px-3 py-1.5">
        <CodeIcon className="text-muted-foreground/60 h-3 w-3" />
        <span className="text-muted-foreground/80 truncate font-mono text-xs">
          {filename}
        </span>
        {showDiff && stats && (stats.added > 0 || stats.removed > 0) && (
          <span className="ml-1 shrink-0 font-mono text-[10px]">
            <span className="text-emerald-400">+{stats.added}</span>{" "}
            <span className="text-red-400">−{stats.removed}</span>
          </span>
        )}
        <div className="ml-auto flex shrink-0 items-center gap-1">
          {diff !== null && (
            <div className="border-border/40 flex items-center rounded border text-[10px]">
              <button
                onClick={() => setMode("diff")}
                className={cn(
                  "px-1.5 py-0.5 transition-colors",
                  mode === "diff"
                    ? "bg-muted text-foreground"
                    : "text-muted-foreground/60 hover:text-muted-foreground",
                )}
              >
                {t.agentComputer.editor.diff}
              </button>
              <button
                onClick={() => setMode("file")}
                className={cn(
                  "px-1.5 py-0.5 transition-colors",
                  mode === "file"
                    ? "bg-muted text-foreground"
                    : "text-muted-foreground/60 hover:text-muted-foreground",
                )}
              >
                {t.agentComputer.editor.file}
              </button>
            </div>
          )}
          {!showDiff && lineCount > 1 && (
            <span className="text-muted-foreground/50 text-[10px]">
              {t.agentComputer.editor.lines(lineCount)}
            </span>
          )}
          {isWriting && (
            <span className="inline-flex items-center gap-1 rounded bg-blue-500/20 px-1.5 py-0.5 text-[10px] text-blue-400">
              <span className="h-1.5 w-1.5 animate-pulse rounded-full bg-blue-400" />
              {t.agentComputer.editor.writing}
            </span>
          )}
        </div>
      </div>
      {/* Code content */}
      <div className="min-h-0 flex-1 overflow-y-auto">
        {showDiff && diff ? (
          <DiffView lines={diff} />
        ) : exists && content ? (
          <pre
            className={cn(
              "p-3 font-mono text-[11px] leading-relaxed break-all whitespace-pre-wrap",
              codeColor,
            )}
          >
            {content}
            {isWriting && <span className="animate-pulse text-white">█</span>}
          </pre>
        ) : (
          <div className="flex h-full items-center justify-center">
            <LoaderCircleIcon className="text-muted-foreground/30 h-5 w-5 animate-spin" />
          </div>
        )}
        <div ref={bottomRef} />
      </div>
    </div>
  );
}

