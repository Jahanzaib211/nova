"use client";

import type { AIMessage } from "@langchain/langgraph-sdk";
import { useEffect } from "react";


import { getBackendBaseURL, getBaseOrigin } from "@/core/config";
import type { SubtaskUpdateSource } from "@/core/tasks/context";
import type { Subtask } from "@/core/tasks/types";

/**
 * One shape for every subagent lifecycle event, whichever transport delivered
 * it: the LangGraph custom-event stream (SSE) or the thread-scoped
 * computer WebSocket (/api/threads/{id}/computer-ws).
 */
export type TaskLifecycleEvent = {
  type: string;
  task_id: string;
  message?: AIMessage;
  description?: string;
  result?: string;
  error?: string;
  /**
   * Which todo rows this delegation was bound to at dispatch (indexes into
   * the thread's todo list). Absent on older backends - treat as "unknown",
   * never as an empty strike set.
   */
  todo_indexes?: number[];
};

/** Dev-server transition pushed by the gateway (channel:"browser"). */
export type DevServerEvent = {
  thread_id: string;
  label?: string;
  status: "starting" | "ready" | "error" | "stopped" | "crashed" | string;
  port?: number;
  container_port?: number;
};

/** Observation fact pushed by the gateway (channel:"workspace"). */
export type WorkspaceObservationEvent = {
  thread_id: string;
  tool: string;
  path?: string | null;
  ts?: string;
  state?: string | null;
};

type UpdateSubtask = (
  patch: Partial<Subtask> & { id: string },
  source?: SubtaskUpdateSource,
) => void;

export type ComputerEventHandlers = {
  /** Subagent lifecycle; applied to the subtask store via applyTaskEvent. */
  updateSubtask: UpdateSubtask;
  /** Dev-server transition - invalidate dev-status/dev-servers caches here. */
  onDevServer?: (event: DevServerEvent) => void;
  /** Workspace observation - invalidate file/preview caches here. */
  onObservation?: (event: WorkspaceObservationEvent) => void;
};

/** Is this a subagent lifecycle event we can apply? */
export function isTaskLifecycleEvent(event: unknown): event is TaskLifecycleEvent {
  return (
    typeof event === "object" &&
    event !== null &&
    "type" in event &&
    typeof (event as { type?: unknown }).type === "string" &&
    (event as { type: string }).type.startsWith("task_") &&
    "task_id" in event
  );
}

/**
 * Apply one lifecycle event to the subtask store. The single writer used by
 * BOTH transports so their semantics cannot drift - the FSM in tasks/context
 * dedupes double delivery by design ("if either arrives, the card settles").
 */
export function applyTaskEvent(
  e: TaskLifecycleEvent,
  updateSubtask: UpdateSubtask,
): void {
  const todoIndexes =
    Array.isArray(e.todo_indexes) &&
    e.todo_indexes.every((n) => typeof n === "number")
      ? e.todo_indexes
      : undefined;
  switch (e.type) {
    case "task_started":
      updateSubtask(
        {
          id: e.task_id,
          status: "in_progress",
          ...(todoIndexes ? { todoIndexes } : {}),
          ...(e.description !== undefined ? { description: e.description } : {}),
        },
        "result",
      );
      return;
    case "task_running":
      updateSubtask(
        {
          id: e.task_id,
          status: "in_progress",
          ...(e.message !== undefined ? { latestMessage: e.message } : {}),
        },
        "result",
      );
      return;
    case "task_completed":
      updateSubtask(
        {
          id: e.task_id,
          status: "completed",
          ...(todoIndexes ? { todoIndexes } : {}),
          ...(e.result !== undefined ? { result: e.result } : {}),
        },
        "result",
      );
      return;
    // Cancelled and timed-out are failures as far as the card is concerned.
    case "task_failed":
    case "task_cancelled":
    case "task_timed_out":
      updateSubtask(
        {
          id: e.task_id,
          status: "failed",
          ...(todoIndexes ? { todoIndexes } : {}),
          ...(e.error ? { error: e.error } : {}),
        },
        "result",
      );
      return;
    default:
      return;
  }
}

/**
 * Live Agent's Computer feed over ONE thread-scoped WebSocket.
 *
 * Channels multiplexed on /api/threads/{id}/computer-ws:
 * - task_* lifecycle events -> updateSubtask (same FSM as the SSE path)
 * - channel:"browser"       -> onDevServer (dev-server transitions, push)
 * - channel:"workspace"     -> onObservation (tool facts that invalidate
 *                              stale previews instantly)
 *
 * Reconnects with capped exponential backoff. A gateway older than this
 * build has no such route - the failed upgrade stays invisible and the SSE
 * fallbacks keep working.
 */
export function useComputerEvents(
  threadId: string | null | undefined,
  handlers: ComputerEventHandlers,
): void {
  useEffect(() => {
    if (!threadId) return;

    let closed = false;
    let socket: WebSocket | null = null;
    let retryTimer: ReturnType<typeof setTimeout> | undefined;
    let attempts = 0;

    const base = getBackendBaseURL() || getBaseOrigin();
    const wsBase = base.replace(/^http/, "ws");
    const url = `${wsBase}/api/threads/${encodeURIComponent(threadId)}/computer-ws`;

    const handleFrame = (raw: string) => {
      let parsed: unknown;
      try {
        parsed = JSON.parse(raw);
      } catch {
        return; // heartbeat / non-JSON frame
      }
      if (isTaskLifecycleEvent(parsed)) {
        applyTaskEvent(parsed, handlers.updateSubtask);
        return;
      }
      if (
        typeof parsed === "object" &&
        parsed !== null &&
        (parsed as { channel?: string }).channel === "browser"
      ) {
        handlers.onDevServer?.(parsed as DevServerEvent);
        return;
      }
      if (
        typeof parsed === "object" &&
        parsed !== null &&
        (parsed as { channel?: string }).channel === "workspace"
      ) {
        handlers.onObservation?.(parsed as WorkspaceObservationEvent);
      }
    };

    const connect = () => {
      if (closed) return;
      try {
        socket = new WebSocket(url);
      } catch {
        scheduleRetry();
        return;
      }

      socket.onmessage = (message) => handleFrame(String(message.data));
      socket.onopen = () => {
        attempts = 0;
      };
      socket.onclose = () => {
        socket = null;
        scheduleRetry();
      };
      socket.onerror = () => {
        /* onclose follows */
      };
    };

    const scheduleRetry = () => {
      if (closed) return;
      const delay = Math.min(15000, 1000 * 2 ** attempts++);
      retryTimer = setTimeout(connect, delay);
    };

    connect();

    return () => {
      closed = true;
      clearTimeout(retryTimer);
      if (socket) {
        socket.onclose = null;
        socket.onerror = null;
        socket.onmessage = null;
        socket.close();
        socket = null;
      }
    };
    // Handlers come from the consumer; wrap them in a stable ref so a new
    // closure per render does not rebuild the socket.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [threadId]);
}
