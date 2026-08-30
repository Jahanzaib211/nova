"use client";

import { LoaderCircleIcon } from "lucide-react";
import { useCallback, useEffect, useRef, useState } from "react";

import { useI18n } from "@/core/i18n/hooks";
import type { SandboxLogStatus } from "@/core/sandbox/hooks";
import { useSandboxTerminalUrl } from "@/core/sandbox/hooks";
import type { AgentActivityEvent } from "@/core/threads/hooks";
import { isTerminalTool } from "@/core/threads/tool-surface";
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
export { isTerminalTool } from "@/core/threads/tool-surface";

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
  serverCommandCount,
  logStatus,
}: {
  events: AgentActivityEvent[];
  threadId: string;
  /** Accepted for uniformity with the other tabs; see the note on `shellOpen`. */
  active?: boolean;
  /**
   * Exact command total pushed over computer-ws. Undefined while the socket
   * is down - the header then shows an approximate window count prefixed
   * with ~ so it can never read as exact.
   */
  serverCommandCount?: number;
  /**
   * Connection state of the sandbox.log SSE -- the *only* transport that
   * carries command text (computer-ws carries counters and facts, never
   * output). A dead stream and an idle agent used to render identically, so
   * "the terminal never shows anything" was indistinguishable from "nothing
   * ran". Distinguish them.
   */
  logStatus?: SandboxLogStatus;
}) {
  const { t } = useI18n();
  const bottomRef = useRef<HTMLDivElement>(null);
  const terminalEvents = events.filter((e) => isTerminalTool(e.type));
  // Deterministic total pushed by the gateway (survives the 200-event window
  // rolling and SSE reconnects). Falls back to the local window while the
  // socket is down - prefixed with ~ so it can never read as exact.
  const serverTotal = serverCommandCount;
  const runningCount = terminalEvents.filter(
    (e) => e.status === "running",
  ).length;
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
  const {
    terminal: terminalUrl,
    refetch: refetchTerminalUrl,
    isFetching: terminalUrlFetching,
  } = useSandboxTerminalUrl(threadId, shellOpen);

  // A ttyd that dies (sandbox recycled, container restarted) leaves the iframe
  // showing a dead page with no error state and nothing to click -- the pane
  // just sat there. `onError` covers a hard load failure; the explicit
  // Reconnect button covers the rest, because an iframe pointed at a proxy that
  // answers 502 fires `load`, not `error`, and cannot be inspected
  // cross-document to tell the difference.
  const [shellFailed, setShellFailed] = useState(false);
  const reconnectShell = useCallback(() => {
    setShellFailed(false);
    refetchTerminalUrl();
  }, [refetchTerminalUrl]);

  // A new URL means a new session; clear any error from the previous one.
  useEffect(() => {
    setShellFailed(false);
  }, [terminalUrl]);

  // Extracted so the dependency is a plain value the linter can check. Inline,
  // `terminalEvents.at(-1)?.output` is a complex expression that exhaustive-deps
  // cannot verify, which is how a dependency silently goes stale.
  const lastTerminalOutput = terminalEvents.at(-1)?.output;

  useEffect(() => {
    // Gated on `active`: every tab stays mounted and is only hidden via CSS
    // (see frontend/CLAUDE.md), so an ungated scroll drags a hidden subtree on
    // every event and jolts the surrounding panel.
    if (!active) return;
    bottomRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [active, terminalEvents.length, lastTerminalOutput]);

  const ModeToggle = (
    <div className="border-border/30 flex shrink-0 items-center gap-1 border-b bg-black/40 px-2 py-1">
      <span className="text-muted-foreground/50 mr-auto font-mono text-[10px]">
        {t.agentComputer.terminal.tab}
      </span>
      {/* Command count: total captured + work in flight. The panel badge
          shows only the live number; here the operator gets both without
          counting prompt rows by eye. */}
      <span className="text-muted-foreground/40 font-mono text-[10px]">
        {serverTotal !== undefined
          ? t.agentComputer.terminal.counts(serverTotal, runningCount)
          : `~${terminalEvents.length}`}
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
      {shellOpen && (
        <button
          type="button"
          onClick={reconnectShell}
          disabled={terminalUrlFetching}
          className="border-border/40 text-muted-foreground/60 hover:text-muted-foreground ml-1 rounded border px-1.5 py-0.5 text-[10px] disabled:opacity-50"
        >
          {terminalUrlFetching
            ? t.common.loading
            : t.agentComputer.terminal.reconnect}
        </button>
      )}
    </div>
  );

  if (mode === "shell") {
    return (
      <div className="flex h-full flex-col">
        {ModeToggle}
        <div className="min-h-0 flex-1 bg-black">
          {shellFailed ? (
            <div className="flex h-full flex-col items-center justify-center gap-3 text-center">
              <p className="text-muted-foreground/60 text-xs">
                {t.agentComputer.terminal.shellDisconnected}
              </p>
              <button
                type="button"
                onClick={reconnectShell}
                disabled={terminalUrlFetching}
                className="border-border/40 text-muted-foreground hover:bg-muted/20 rounded border px-2 py-1 text-[11px] disabled:opacity-50"
              >
                {terminalUrlFetching
                  ? t.common.loading
                  : t.agentComputer.terminal.reconnect}
              </button>
            </div>
          ) : terminalUrl ? (
            <iframe
              key={terminalUrl}
              src={terminalUrl}
              title={t.agentComputer.terminal.interactiveTitle}
              className="h-full w-full border-0"
              onError={() => setShellFailed(true)}
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
          {logStatus === "reconnecting" ? (
            <div>
              <p className="text-xs font-medium text-amber-400/70">
                {t.agentComputer.terminal.streamDown}
              </p>
              <p className="text-muted-foreground/40 mt-1 text-[10px]">
                {t.agentComputer.terminal.streamDownHint}
              </p>
            </div>
          ) : (
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
          )}
        </div>
      </div>
    );
  }

  return (
    <div className="flex h-full flex-col bg-black/70">
      {ModeToggle}
      <div className="min-h-0 flex-1 overflow-y-auto p-3 font-mono text-xs">
        {terminalEvents.map((event, i) => (
          <div key={event.id || `${event.type}-${i}`} className="mb-4">
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
