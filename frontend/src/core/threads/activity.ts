// Derives the Agent's Computer live activity feed from the message stream.
//
// Why this exists: the tool-activity events that drive the Agent's Computer
// (Terminal spinner, Editor "writing…", auto-open, current-tool status) used to
// come from the LangGraph `events` stream mode via `onLangChainEvent`
// (`on_tool_start`/`on_tool_end`). The gateway worker does NOT emit `events`
// mode (deerflow/runtime/runs/worker.py skips it — it needs astream_events,
// which can't co-produce `values`), so that callback never fires at runtime and
// the whole live-tool layer went dark (panel never auto-opened, no spinner).
//
// The `messages` stream mode IS supported and already renders the chat's tool
// cards. This module reconstructs the same tool-activity events from
// `thread.messages`: each AI `tool_calls` entry becomes a `running` event, and
// its matching `ToolMessage` result flips it to `done`/`error`. Deterministic,
// backend-free, and the single source of truth for the panel's live layer.

import type { AIMessage, Message, ToolMessage } from "@langchain/langgraph-sdk";

import type { AgentActivityEvent } from "./hooks";
import { isTerminalTool } from "./tool-surface";

export type { AgentActivityEvent };

/** Human-readable one-liner for a tool activity, matching the chat's phrasing. */
export function buildActivitySummary(
  name: string,
  { path, cmd }: { path?: string | null; cmd?: string | null },
): string {
  const filename = path?.split("/").at(-1);
  if (name === "write_file")
    return filename ? `Writing ${filename}` : "Writing file";
  if (name === "str_replace")
    return filename ? `Editing ${filename}` : "Editing file";
  if (name === "read_file")
    return filename ? `Reading ${filename}` : "Reading file";
  if (name === "bash" || name === "execute_command")
    return cmd ? `$ ${cmd.slice(0, 60)}` : "Running command";
  // The shell_* family is the modern execution path; its args carry the
  // command under the same `command`/`cmd` keys as bash. Classify — don't
  // enumerate: the next family member summarizes itself.
  if (isTerminalTool(name) && name.startsWith("shell_"))
    return cmd ? `$ ${cmd.slice(0, 60)}` : "Shell session";
  if (name === "ls") return "Listing directory";
  if (name === "search_files") return "Searching files";
  if (name === "grep" || name === "grep_files") return "Searching content";
  if (name === "glob") return "Finding files by pattern";
  if (name === "scaffold_project") return "Scaffolding project";
  if (name === "task") return "Delegating to subagent";
  return name;
}

function toolResultText(message: ToolMessage): string {
  const content = message.content as unknown;
  if (typeof content === "string") return content;
  if (Array.isArray(content)) {
    return content
      .map((block) => {
        if (typeof block === "string") return block;
        if (block && typeof block === "object" && "text" in block) {
          const text = (block as { text: unknown }).text;
          return typeof text === "string" ? text : "";
        }
        return "";
      })
      .join("");
  }
  return "";
}

const _str = (v: unknown): string | null => (typeof v === "string" ? v : null);

/**
 * Reconstruct the Agent's Computer activity feed from the thread's messages.
 *
 * One event per AI tool call, keyed by the tool_call id so a call and its result
 * collapse into a single event whose `status` reflects whether the result has
 * arrived. `limit` keeps the tail bounded (mirrors the old 200-event cap).
 */
export function messagesToActivityEvents(
  messages: Message[],
  { limit = 200 }: { limit?: number } = {},
): AgentActivityEvent[] {
  // Index tool results by the tool_call id they answer.
  const resultById = new Map<string, { output: string; isError: boolean }>();
  for (const m of messages) {
    if (m.type !== "tool") continue;
    const tm = m;
    if (!tm.tool_call_id) continue;
    const output = toolResultText(tm);
    // Trust the tool's own status field. The upstream LangGraph SDK classifies
    // terminal state the same way (``SubagentManager`` reads ``tm.status``
    // only). The old ``output.startsWith("Error:")`` heuristic was wrong: 40+
    // sandbox tools legitimately return ``f"Error: …"`` on success paths
    // (policy refusals, missing-target reads, etc.), and the heuristic
    // painted every such tool step red, made the Activity-tab card flash
    // "failed" while the chat card stayed green, and dropped a running event
    // out of the "running" set — the Agent's Computer went idle mid-task.
    // Real errors come from ``ToolErrorHandlingMiddleware._build_error_message``
    // and arrive with ``status: "error"`` already set; the prefix fallback
    // is redundant.
    const isError = (tm as { status?: string }).status === "error";
    resultById.set(tm.tool_call_id, { output, isError });
  }

  const events: AgentActivityEvent[] = [];
  for (const m of messages) {
    if (m.type !== "ai") continue;
    const calls = (m as AIMessage).tool_calls ?? [];
    for (const call of calls) {
      const name = call.name;
      const args = (call.args ?? {}) as Record<string, unknown>;
      const path = _str(args.path);
      const cmd = _str(args.command);
      const id = call.id ?? `${name}-${events.length}`;
      const result = call.id ? resultById.get(call.id) : undefined;
      const status: AgentActivityEvent["status"] = !result
        ? "running"
        : result.isError
          ? "error"
          : "done";
      events.push({
        id,
        // messages carry no wall-clock timestamp; the panel keys running-event
        // dedupe on ts|type|summary — empty ts is fine because running events
        // are superseded by the sandbox.log "done" line, never merged with it.
        ts: "",
        type: name,
        path,
        summary: buildActivitySummary(name, { path, cmd }),
        output: result?.output ?? "",
        status,
      });
    }
  }

  return events.length > limit ? events.slice(events.length - limit) : events;
}

/** The tool currently executing (last running event), or null when idle. */
export function currentToolFromActivity(
  events: AgentActivityEvent[],
): string | null {
  for (let i = events.length - 1; i >= 0; i--) {
    if (events[i]?.status === "running") return events[i]!.type;
  }
  return null;
}

/** Path of the most recent write_file activity, for the Editor's active file. */
export function activeWriteFilePathFromActivity(
  events: AgentActivityEvent[],
): string | null {
  for (let i = events.length - 1; i >= 0; i--) {
    const e = events[i];
    if (e?.type === "write_file" && e.path) return e.path;
  }
  return null;
}
