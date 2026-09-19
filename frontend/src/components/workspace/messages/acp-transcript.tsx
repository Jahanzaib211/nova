"use client";

import { useEffect, useRef } from "react";

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
