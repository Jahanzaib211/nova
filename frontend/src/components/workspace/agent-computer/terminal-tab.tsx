"use client";

import { LoaderCircleIcon } from "lucide-react";
import { useEffect, useRef, useState } from "react";

import { useI18n } from "@/core/i18n/hooks";
import { useSandboxTerminalUrl } from "@/core/sandbox/hooks";
import type { AgentActivityEvent } from "@/core/threads/hooks";
import { cn } from "@/lib/utils";

// Tab 1: Terminal — bash/search/grep events, always expanded
// ──────────────────────────────────────────────────────────
// Note: "task" (delegate to subagent) stays in Activity, not Terminal
//
// Pinned by the 2026-08-14 fix: a run that only used ``write_file`` /
// ``str_replace`` / ``read_file`` previously left this tab empty because the
// filter was too narrow. Those tools share the same ``sandbox.log`` SSE feed
// as bash, so the user expects to see them here; the Activity tab still owns
// the high-signal "Writing index.html" cards.
export const TERMINAL_TOOLS = new Set([
  "bash",
  "execute_command",
  "search_files",
  "grep_files",
  "read_file",
  "write_file",
  "str_replace",
]);

/**
 * Class names for the terminal output ``<pre>`` block. Extracted so it can be
 * unit-tested without rendering React — the upstream classification fix in
 * ``activity.ts`` is what guarantees we never see ``status: "error"`` for a
 * tool that returned ``"Error: …"`` on a success path, but the styling here
 * stays defensive in case any future tool emits the literal error state.
 */
export function terminalOutputClass(status: string): string {
  return cn(
    "border-l pl-4 leading-relaxed break-all whitespace-pre-wrap",
    status === "error"
      ? "border-red-900/50 text-red-400"
      : "border-emerald-900/30 text-emerald-300/80",
  );
}

export function Terminal({
  events,
  threadId,
  active = true,
}: {
  events: AgentActivityEvent[];
  threadId: string;
  /** Accepted for uniformity with the other tabs; see the note on `shellOpen`. */
  active?: boolean;
}) {
  const { t } = useI18n();
  const bottomRef = useRef<HTMLDivElement>(null);
  const terminalEvents = events.filter((e) => TERMINAL_TOOLS.has(e.type));
  // "shell" = the sandbox's real interactive ttyd terminal (type into it live);
  // "stream" = the agent's command output log.
  const [mode, setMode] = useState<"stream" | "shell">("stream");
  // Deliberately NOT gated on `active`.
  //
  // Gating the ttyd iframe on tab visibility does stop it holding a PTY while
  // hidden — but it also tears the session down and rebuilds it on every tab
  // switch, so the user loses their shell: scrollback, working directory, and
  // anything still running. An interactive terminal is *supposed* to persist;
  // that is the whole feature. `mode === "shell"` already means nothing is
  // started until the user explicitly opens the shell, which is the gate that
  // matters. `active` is still accepted so the panel can pass it uniformly.
  const shellOpen = mode === "shell";
  const { terminal: terminalUrl } = useSandboxTerminalUrl(threadId, shellOpen);

  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [terminalEvents.length, terminalEvents.at(-1)?.output]);

  const ModeToggle = (
    <div className="border-border/30 flex shrink-0 items-center gap-1 border-b bg-black/40 px-2 py-1">
      <span className="text-muted-foreground/50 mr-auto font-mono text-[10px]">
        {t.agentComputer.terminal.tab}
      </span>
      <div className="border-border/40 flex items-center rounded border text-[10px]">
        <button
          onClick={() => setMode("stream")}
          className={cn(
            "px-1.5 py-0.5",
            mode === "stream"
              ? "bg-muted text-foreground"
              : "text-muted-foreground/60 hover:text-muted-foreground",
          )}
        >
          {t.agentComputer.terminal.stream}
        </button>
        <button
          onClick={() => setMode("shell")}
          className={cn(
            "px-1.5 py-0.5",
            mode === "shell"
              ? "bg-muted text-foreground"
              : "text-muted-foreground/60 hover:text-muted-foreground",
          )}
        >
          {t.agentComputer.terminal.shell}
        </button>
      </div>
    </div>
  );

  if (mode === "shell") {
    return (
      <div className="flex h-full flex-col">
        {ModeToggle}
        <div className="min-h-0 flex-1 bg-black">
          {terminalUrl ? (
            <iframe
              key={terminalUrl}
              src={terminalUrl}
              title={t.agentComputer.terminal.interactiveTitle}
              className="h-full w-full border-0"
            />
          ) : (
            <div className="flex h-full items-center justify-center">
              <LoaderCircleIcon className="text-muted-foreground/30 h-5 w-5 animate-spin" />
            </div>
          )}
        </div>
      </div>
    );
  }

  if (terminalEvents.length === 0) {
    return (
      <div className="flex h-full flex-col">
        {ModeToggle}
        <div className="flex flex-1 flex-col items-center justify-center gap-3 bg-black/50 text-center">
          <div className="animate-pulse font-mono text-xl text-emerald-400/30">
            ▮
          </div>
          <div>
            <p className="text-muted-foreground/60 text-xs font-medium">
              {t.agentComputer.terminal.noOutput}
            </p>
            <p className="text-muted-foreground/40 mt-1 text-[10px]">
              {t.agentComputer.terminal.noOutputHint}{" "}
              <span className="text-emerald-400/70">
                {t.agentComputer.terminal.shell}
              </span>
            </p>
          </div>
        </div>
      </div>
    );
  }

  return (
    <div className="flex h-full flex-col bg-black/70">
      {ModeToggle}
      <div className="min-h-0 flex-1 overflow-y-auto p-3 font-mono text-xs">
        {terminalEvents.map((event, i) => (
          <div key={i} className="mb-4">
            {/* Command prompt line */}
            <div className="mb-1.5 flex items-center gap-1.5">
              <span className="text-emerald-500/70 select-none">❯</span>
              <span
                className={cn(
                  "flex-1 font-medium",
                  event.type === "search_files" || event.type === "grep_files"
                    ? "text-orange-400"
                    : "text-emerald-400",
                )}
              >
                {event.summary}
              </span>
              {event.status === "running" && (
                <LoaderCircleIcon className="h-3 w-3 shrink-0 animate-spin text-emerald-400/50" />
              )}
              <span className="text-muted-foreground/30 shrink-0 text-[10px]">
                {event.ts}
              </span>
            </div>
            {/* Output — always shown, no click needed */}
            {event.output ? (
              <pre className={terminalOutputClass(event.status)}>
                {event.output}
              </pre>
            ) : event.status === "running" ? (
              <div className="pl-4 text-emerald-400/40">
                <span className="animate-pulse">
                  {t.agentComputer.terminal.running}
                </span>
              </div>
            ) : null}
          </div>
        ))}
        <div ref={bottomRef} />
        <span className="animate-pulse text-emerald-400/60">▮</span>
      </div>
    </div>
  );
}
