import type { AIMessage, Message } from "@langchain/langgraph-sdk";

// Helpers
// ──────────────────────────────────────────────────────────

export function getActiveFilePath(messages: Message[]): string | null {
  for (let i = messages.length - 1; i >= 0; i--) {
    const msg = messages[i];
    if (!msg || msg.type !== "ai") continue;
    const calls = (msg as AIMessage).tool_calls ?? [];
    for (let j = calls.length - 1; j >= 0; j--) {
      const call = calls[j];
      if (!call) continue;
      if (
        call.name === "write_file" ||
        call.name === "str_replace" ||
        call.name === "read_file"
      ) {
        const path = (call.args as Record<string, unknown>)?.path;
        if (typeof path === "string") return path;
      }
    }
  }
  return null;
}

// The most recent file edit, sourced from tool-call args already in state — used
// by the Editor's live-diff view (str_replace carries old/new; write_file is all-add).
export type ActiveEdit =
  | { path: string; kind: "str_replace"; oldStr: string; newStr: string }
  | { path: string; kind: "write_file"; content: string };

export function getActiveEdit(messages: Message[]): ActiveEdit | null {
  for (let i = messages.length - 1; i >= 0; i--) {
    const msg = messages[i];
    if (!msg || msg.type !== "ai") continue;
    const calls = (msg as AIMessage).tool_calls ?? [];
    for (let j = calls.length - 1; j >= 0; j--) {
      const call = calls[j];
      if (!call) continue;
      const args = (call.args ?? {}) as Record<string, unknown>;
      const path = typeof args.path === "string" ? args.path : null;
      if (!path) continue;
      if (
        call.name === "str_replace" &&
        typeof args.old_str === "string" &&
        typeof args.new_str === "string"
      ) {
        return {
          path,
          kind: "str_replace",
          oldStr: args.old_str,
          newStr: args.new_str,
        };
      }
      if (call.name === "write_file" && typeof args.content === "string") {
        return { path, kind: "write_file", content: args.content };
      }
    }
  }
  return null;
}
