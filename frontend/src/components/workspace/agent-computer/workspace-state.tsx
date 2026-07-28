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
  const sseEvents = useSandboxLogs(threadId);
  const mergedEvents = useMemo<AgentActivityEvent[]>(() => {
    const fromLog: AgentActivityEvent[] = sseEvents.map(
      (e: SandboxEvent, i) => ({
        id: `log-${i}-${e.ts}`,
        ts: e.ts,
        type: e.type,
        path: e.path,
        summary: e.summary,
        output: e.output,
        status: "done",
      }),
    );
    const seen = new Set(
      sseEvents.map((e) => `${e.ts}|${e.type}|${e.summary}`),
    );
    const running = activityEvents.filter(
      (e) =>
        e.status === "running" && !seen.has(`${e.ts}|${e.type}|${e.summary}`),
    );
    return [...fromLog, ...running];
  }, [sseEvents, activityEvents]);

  // Stream todos win; the checkpoint endpoint fills the gap after a
  // refresh/reconnect when thread.values has not hydrated yet.
  const sandboxTodo = useSandboxTodo(streamTodos.length > 0 ? null : threadId);
  const todos = useMemo<Todo[]>(() => {
    if (streamTodos.length > 0) return streamTodos;
    return sandboxTodo.todos.map((todo) => ({
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
      taskProgress,
      verifyResult,
      llmError,
    }),
    [
      threadId,
      activityEvents,
      mergedEvents,
      todos,
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
