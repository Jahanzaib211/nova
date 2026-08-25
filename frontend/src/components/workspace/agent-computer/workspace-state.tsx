"use client";

import { createContext, useContext, useMemo } from "react";

import type {
  LlmError,
  TaskProgress,
  VerifyResult,
} from "@/components/workspace/messages/context";
import { useThread } from "@/components/workspace/messages/context";
import {
  useSandboxLogs,
  useSandboxTodo,
  type SandboxEvent,
  type SandboxLogStatus,
} from "@/core/sandbox/hooks";
import type { AgentActivityEvent } from "@/core/threads/hooks";
import type { Todo } from "@/core/todos";

/**
 * Central workspace state for the Agent Computer panel (C10 Batch 0).
 *
 * One provider owns the thread's workspace-derived state instead of
 * chat-box prop-drilling values the ThreadContext already carries:
 * - activityEvents / taskProgress / verifyResult / llmError come from
 *   the existing ThreadContext (single source, no re-passing).
 * - mergedEvents (sandbox.log SSE merged with in-flight running events)
 *   is computed once here — previously an inline hack in the panel.
 * - todos fall back to the /api/sandbox/todo checkpoint endpoint when the
 *   stream carries none (post-refresh/reconnect), reviving useSandboxTodo.
 */
export interface WorkspaceState {
  threadId: string;
  /** Raw in-flight activity events from the LangGraph stream. */
  activityEvents: AgentActivityEvent[];
  /** sandbox.log-backed history merged with in-flight running events. */
  mergedEvents: AgentActivityEvent[];
  todos: Todo[];
  /** Connection state of the sandbox.log stream, so the Terminal can say
      "reconnecting…" rather than sitting silently empty. */
  logStatus: SandboxLogStatus;
  taskProgress: TaskProgress | null;
  verifyResult: VerifyResult | null;
  llmError: LlmError | null;
}

const WorkspaceStateContext = createContext<WorkspaceState | undefined>(
  undefined,
);

export function useWorkspaceState(): WorkspaceState {
  const state = useContext(WorkspaceStateContext);
  if (!state) {
    throw new Error(
      "useWorkspaceState must be used within a WorkspaceStateProvider",
    );
  }
  return state;
}

/**
 * Merge the sandbox.log history with the in-flight LangGraph "running" events.
 *
 * Exported and pure so the two identity rules below can be tested without
 * rendering React — both were silent, intermittent bugs that a render test
 * would not have caught reliably.
 */
/** First occurrence passes through; later identical ids get `#2`, `#3`, … */
function disambiguate(base: string, seen: Map<string, number>): string {
  const count = (seen.get(base) ?? 0) + 1;
  seen.set(base, count);
  return count === 1 ? base : `${base}#${count}`;
}

export function mergeWorkspaceEvents(
  sseEvents: SandboxEvent[],
  activityEvents: AgentActivityEvent[],
): AgentActivityEvent[] {
  const seenIds = new Map<string, number>();
  const fromLog: AgentActivityEvent[] = sseEvents.map((e: SandboxEvent) => ({
    // `uid` is assigned once, when the event is first ingested. The previous
    // key was built from the array index, which shifts for every element the
    // moment the MAX_EVENTS window rolls — remounting the entire list while
    // the user is reading it.
    //
    // The composite fallback collides when two identical commands land in the
    // same second (same ts|type|summary) — duplicate React keys remount rows
    // mid-stream. Disambiguate with an occurrence counter.
    id: e.uid ?? disambiguate(`${e.ts}-${e.type}-${e.summary}`, seenIds),
    ts: e.ts,
    type: e.type,
    path: e.path,
    summary: e.summary,
    output: e.output,
    // A streamed command is still running until its closing frame arrives.
    // Hardcoding "done" here is what made a live `npm install` look finished
    // the instant its first line of output appeared.
    status: e.state === "running" ? "running" : "done",
  }));

  // Suppress the in-flight spinner for work the log already shows. Counted,
  // not set-membership: `ts` has one-second resolution ("%H:%M:%S"), so two
  // identical commands in the same second share a key, and a plain Set let one
  // log line mask both of them. N log entries now mask exactly N.
  const seen = new Map<string, number>();
  for (const e of sseEvents) {
    const key = `${e.ts}|${e.type}|${e.summary}`;
    seen.set(key, (seen.get(key) ?? 0) + 1);
  }
  const running = activityEvents.filter((e) => {
    if (e.status !== "running") return false;
    const key = `${e.ts}|${e.type}|${e.summary}`;
    const remaining = seen.get(key) ?? 0;
    if (remaining > 0) {
      seen.set(key, remaining - 1);
      return false;
    }
    return true;
  });
  return [...fromLog, ...running];
}

export function WorkspaceStateProvider({
  threadId,
  todos: streamTodos,
  children,
}: {
  threadId: string;
  /** Todos from the live LangGraph stream (thread.values.todos). */
  todos: Todo[];
  children: React.ReactNode;
}) {
  const { activityEvents, taskProgress, verifyResult, llmError } = useThread();

  // sandbox.log SSE is the source of truth (real bash output, all tools).
  // Merge with in-flight "running" LangGraph events for the live spinner.
  const { events: sseEvents, status: logStatus } = useSandboxLogs(threadId);
  const mergedEvents = useMemo<AgentActivityEvent[]>(
    () => mergeWorkspaceEvents(sseEvents, activityEvents),
    [sseEvents, activityEvents],
  );

  // Stream todos win; the checkpoint endpoint fills the gap after a
  // refresh/reconnect when thread.values has not hydrated yet.
  const sandboxTodo = useSandboxTodo(streamTodos.length > 0 ? null : threadId);
  const todos = useMemo<Todo[]>(() => {
    if (streamTodos.length > 0) return streamTodos;
    return (sandboxTodo.todos ?? []).map((todo) => ({
      content: todo.description,
      status: todo.status === "done" ? "completed" : todo.status,
    }));
  }, [streamTodos, sandboxTodo.todos]);

  const state = useMemo<WorkspaceState>(
    () => ({
      threadId,
      activityEvents,
      mergedEvents,
      todos,
      logStatus,
      taskProgress,
      verifyResult,
      llmError,
    }),
    [
      threadId,
      activityEvents,
      mergedEvents,
      todos,
      logStatus,
      taskProgress,
      verifyResult,
      llmError,
    ],
  );

  return (
    <WorkspaceStateContext.Provider value={state}>
      {children}
    </WorkspaceStateContext.Provider>
  );
}
