"use client";

import { CodeIcon, LoaderCircleIcon, PencilIcon } from "lucide-react";
import { useEffect, useMemo, useRef, useState } from "react";

import { CodeEditor } from "@/components/workspace/code-editor";
import { useI18n } from "@/core/i18n/hooks";
import { useLiveFileContent } from "@/core/sandbox/hooks";
import { diffStats, lineDiff, type DiffLine } from "@/lib/line-diff";
import { cn } from "@/lib/utils";

import type { ActiveEdit } from "./message-helpers";

// Tab 2: Editor — live code view as agent writes
// ──────────────────────────────────────────────────────────
// Renders an interleaved red/green line diff (zai/cursor style).
function DiffView({ lines }: { lines: DiffLine[] }) {
  return (
    <pre className="min-w-full p-0 font-mono text-[11px] leading-relaxed">
      {lines.map((l, i) => (
        <div
          key={i}
          className={cn(
            "flex px-2 break-all whitespace-pre-wrap",
            l.type === "add" && "bg-success/10 text-success",
            l.type === "del" && "bg-destructive/10 text-destructive/90",
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
  const { content, exists, lineCount, isLoading } = useLiveFileContent(
    threadId,
    filePath,
    activeTab && Boolean(filePath),
  );
  const bottomRef = useRef<HTMLDivElement>(null);
  const filename = filePath?.split("/").at(-1) ?? "";

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
  //
  // Keyed on which edit this is, NOT on the object. `activeEdit` comes from
  // `getActiveEdit(messages)`, which builds a fresh object literal every time
  // it recomputes, and `messages` gets a new identity on every streaming chunk.
  // Depending on the object meant this effect refired continuously while the
  // agent wrote, so a user who clicked "File" to read the whole thing was
  // yanked back to "Diff" a fraction of a second later, every time — the toggle
  // was effectively unusable during streaming.
  //
  // The key includes the tool-call id: path+kind alone could NOT distinguish
  // two consecutive str_replace calls on the same file, so edit #2 silently
  // kept "File" view and its diff badge was never shown.
  const editKey = editForFile
    ? `${editForFile.callId}:${editForFile.path}:${editForFile.kind}`
    : null;
  const [mode, setMode] = useState<"diff" | "file">("diff");
  useEffect(() => {
    if (editKey) setMode("diff");
  }, [editKey]);
  const showDiff = mode === "diff" && diff !== null;

  // Auto-scroll while writing — gated on the tab being visible: every tab
  // stays mounted via CSS `hidden`, and an ungated scrollIntoView drags a
  // hidden subtree on every streamed chunk (the v9.6 Terminal bug class).
  useEffect(() => {
    if (isWriting && activeTab)
      bottomRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [activeTab, content, isWriting, showDiff]);

  if (!filePath) {
    return (
      <div className="flex h-full flex-col items-center justify-center gap-2 text-center">
        <PencilIcon className="text-muted-foreground/30 h-6 w-6" />
        <span className="text-muted-foreground/50 text-xs">
          {t.agentComputer.viewer.startWriting}
        </span>
      </div>
    );
  }

  return (
    <div className="flex h-full flex-col bg-black/50">
      {/* File header */}
      <div className="border-panel-border flex shrink-0 items-center gap-2 border-b bg-black/40 px-3 py-1.5">
        <CodeIcon className="text-muted-foreground/60 h-3 w-3" />
        <span className="text-muted-foreground/80 truncate font-mono text-xs">
          {filename}
        </span>
        {showDiff && stats && (stats.added > 0 || stats.removed > 0) && (
          <span className="ml-1 shrink-0 font-mono text-[10px]">
            <span className="text-success">+{stats.added}</span>{" "}
            <span className="text-destructive">−{stats.removed}</span>
          </span>
        )}
        <div className="ml-auto flex shrink-0 items-center gap-1">
          {diff !== null && (
            <div className="border-panel-border flex items-center rounded border text-[10px]">
              <button
                onClick={() => setMode("diff")}
                className={cn(
                  "px-1.5 py-0.5 transition-colors",
                  mode === "diff"
                    ? "bg-muted text-foreground"
                    : "text-muted-foreground/60 hover:text-muted-foreground",
                )}
              >
                {t.agentComputer.viewer.diff}
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
                {t.agentComputer.viewer.file}
              </button>
            </div>
          )}
          {!showDiff && lineCount > 1 && (
            <span className="text-muted-foreground/50 text-[10px]">
              {t.agentComputer.viewer.lines(lineCount)}
            </span>
          )}
          {isWriting && (
            <span className="bg-info/20 text-info inline-flex items-center gap-1 rounded px-1.5 py-0.5 text-[10px]">
              <span className="bg-info h-1.5 w-1.5 animate-pulse rounded-full" />
              {t.agentComputer.viewer.writing}
            </span>
          )}
        </div>
      </div>
      {/* Code content */}
      <div className="min-h-0 flex-1 overflow-y-auto">
        {showDiff && diff ? (
          <DiffView lines={diff} />
        ) : exists && content ? (
          <div className="relative h-full">
            <CodeEditor
              value={content}
              filename={filePath ?? undefined}
              readonly
              className="h-full"
              settings={{ lineNumbers: true }}
            />
            {isWriting && (
              // The "agent is typing into this file" affordance. It lived in
              // the old <pre> as a trailing block cursor; without it a live
              // write is indistinguishable from a static file.
              <span
                aria-hidden
                className="text-foreground pointer-events-none absolute right-2 bottom-1 animate-pulse font-mono text-xs"
                data-testid="editor-writing-cursor"
              >
                █
              </span>
            )}
          </div>
        ) : isLoading ? (
          // Genuinely still fetching — the only state a spinner means.
          <div className="flex h-full items-center justify-center">
            <LoaderCircleIcon className="text-muted-foreground/30 h-5 w-5 animate-spin" />
          </div>
        ) : !exists ? (
          <div className="flex h-full flex-col items-center justify-center gap-1 px-6 text-center">
            <span className="text-muted-foreground/50 text-xs">
              {t.agentComputer.viewer.fileNotWritten}
            </span>
            <span className="text-muted-foreground/35 font-mono text-[10px] break-all">
              {filePath}
            </span>
          </div>
        ) : (
          // exists === true with empty content: a real, empty file.
          <div className="flex h-full items-center justify-center">
            <span className="text-muted-foreground/40 text-xs">
              {t.agentComputer.viewer.emptyFile}
            </span>
          </div>
        )}
        <div ref={bottomRef} />
      </div>
    </div>
  );
}
