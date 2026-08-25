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
}
