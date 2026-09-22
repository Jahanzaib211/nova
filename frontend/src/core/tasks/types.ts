import type { AIMessage } from "@langchain/langgraph-sdk";

export interface Subtask {
  id: string;
  status: "in_progress" | "completed" | "failed";
  subagent_type: string;
  description: string;
  latestMessage?: AIMessage;
  prompt: string;
  result?: string;
  error?: string;
  /**
   * Todo rows this delegation was bound to at dispatch (indexes into the
   * thread's todo list). Set by the backend on task_started and carried on
   * every terminal event; absent means "unknown binding" — the checklist
   * then leaves the row's own status untouched instead of guessing.
   */
  todoIndexes?: number[];
  /**
   * When this subtask first entered the store (epoch ms).
   *
   * Supersede decides "is this part of the current run?" by looking for the
   * subtask's tool_call id in `thread.messages`. That test is sound once both
   * transports have caught up, but the socket and the message stream are
   * independent: a `task_started` frame routinely lands before the assistant
   * message carrying its tool_call does. Without a timestamp there is no way to
   * tell a genuinely orphaned subtask from one that is simply seconds ahead of
   * its message, and the brand-new subagent gets settled as failed.
   */
  firstSeenAt?: number;
}
