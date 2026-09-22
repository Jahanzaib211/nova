"use client";

import { useEffect, useRef, useState } from "react";

import { useI18n } from "@/core/i18n/hooks";
import type { AcpTranscript } from "@/core/threads/acp-transcript";
import { cn } from "@/lib/utils";

/**
 * What an ACP agent is doing right now, under its `invoke_acp_agent` step.
 *
 * Streamed text is the agent's own words; status lines are the gateway's
 * notes about tool calls and permission decisions. Once the tool result is
 * in, the final answer replaces the stream — the same text, settled.
 */
export function AcpTranscriptView({
  transcript,
  result,
  running,
}: {
  transcript: AcpTranscript | undefined;
  result: string | undefined;
  running: boolean;
}) {
  const { t } = useI18n();
  const bottom = useRef<HTMLDivElement>(null);
  const text = !running && result ? result : (transcript?.text ?? "");
  useEffect(() => {
    if (running) bottom.current?.scrollIntoView({ block: "nearest" });
  }, [running, text]);

  if (!text && (transcript?.statuses.length ?? 0) === 0) {
    return running ? (
      <p className="text-muted-foreground text-xs" data-testid="acp-waiting">
        {t.toolCalls.acp.waiting}
      </p>
    ) : null;
  }
  return (
    <div
      data-testid="acp-transcript"
      data-running={running}
      className="border-panel-border bg-panel mt-2 max-h-64 overflow-y-auto rounded-md border p-2 text-xs"
    >
      {transcript?.statuses.map((line, i) => (
        <div
          key={`${i}-${line}`}
          className={cn(
            "font-mono text-[11px]",
            line.startsWith("permission denied")
              ? "text-warning"
              : "text-muted-foreground",
          )}
        >
          · {line}
        </div>
      ))}
      {text && (
        <pre className="mt-1 font-sans break-words whitespace-pre-wrap">
          {text}
          {running && <span className="animate-pulse">▍</span>}
        </pre>
      )}
      <div ref={bottom} />
    </div>
  );
}

/**
 * The turn is running *on* an ACP runtime (Claude Code, OpenClaw) rather than
 * calling one as a tool. There is no tool-call step to hang the transcript
 * under and no assistant message until the runtime finishes, so this card
 * stands in for "is thinking": which runtime, how many actions it has taken,
 * its latest tool calls / permission decisions, and its text as it streams.
 */
export function RuntimeTranscriptCard({
  agent,
  transcript,
}: {
  agent: string;
  transcript: AcpTranscript;
}) {
  const { t } = useI18n();
  const bottom = useRef<HTMLDivElement>(null);
  const [expanded, setExpanded] = useState(false);
  const statuses = transcript.statuses;
  const shown = expanded ? statuses : statuses.slice(-4);
  const latest = statuses[statuses.length - 1];
  const thinking = latest?.startsWith("thinking:")
    ? latest.slice("thinking:".length).trim()
    : null;
  useEffect(() => {
    bottom.current?.scrollIntoView({ block: "nearest" });
  }, [transcript.text, statuses.length]);

  return (
    <div
      data-testid="runtime-transcript"
      data-agent={agent}
      className="border-panel-border bg-panel mx-4 my-2 rounded-xl border p-3 text-xs"
    >
      <div className="flex items-center gap-2">
        <span className="bg-primary/70 size-2 animate-pulse rounded-full" />
        <span className="font-medium">
          {t.toolCalls.acp.runtimeWorking(runtimeDisplayName(agent))}
        </span>
        <span className="text-muted-foreground font-mono text-[11px]">
          · {t.toolCalls.acp.actions(transcript.actionCount)}
        </span>
        {statuses.length > 4 && (
          <button
            type="button"
            className="text-muted-foreground ml-auto text-[11px] underline-offset-2 hover:underline"
            onClick={() => setExpanded((v) => !v)}
          >
            {expanded ? t.toolCalls.acp.showLess : t.toolCalls.acp.showAll}
          </button>
        )}
      </div>
      {shown.length > 0 && (
        <div className="mt-2 max-h-40 overflow-y-auto">
          {shown.map((line, i) => (
            <div
              key={`${i}-${line}`}
              className={cn(
                "truncate font-mono text-[11px]",
                line.startsWith("permission denied") ||
                  line.includes("unavailable")
                  ? "text-warning"
                  : "text-muted-foreground",
              )}
              title={line}
            >
              · {line}
            </div>
          ))}
        </div>
      )}
      {thinking && !transcript.text && (
        <p className="text-muted-foreground mt-2 line-clamp-3 italic">
          {thinking}
        </p>
      )}
      {transcript.text && (
        <pre className="mt-2 max-h-72 overflow-y-auto font-sans break-words whitespace-pre-wrap">
          {transcript.text}
          <span className="animate-pulse">▍</span>
        </pre>
      )}
      <div ref={bottom} />
    </div>
  );
}

/** `claude_code` → "Claude Code"; anything else keeps its id readable. */
export function runtimeDisplayName(agent: string): string {
  if (agent === "claude_code") return "Claude Code";
  if (agent === "openclaw") return "OpenClaw";
  return agent.replace(/[_-]+/g, " ");
}
