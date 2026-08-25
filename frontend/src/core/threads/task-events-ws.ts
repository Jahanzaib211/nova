"use client";

import type { AIMessage } from "@langchain/langgraph-sdk";
import { useEffect } from "react";


import { getBackendBaseURL, getBaseOrigin } from "@/core/config";
import type { SubtaskUpdateSource } from "@/core/tasks/context";
import type { Subtask } from "@/core/tasks/types";

/**
 * One shape for every subagent lifecycle event, whichever transport delivered
 * it: the LangGraph custom-event stream (SSE) or the thread-scoped
 * tasks WebSocket (/api/threads/{id}/tasks-ws).
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
   * the thread's todo list). Absent on older backends — treat as "unknown",
   * never as an empty strike set.
   */
  todo_indexes?: number[];
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

type UpdateSubtask = (
  patch: Partial<Subtask> & { id: string },
  source?: SubtaskUpdateSource,
) => void;

/**
 * Apply one lifecycle event to the subtask store. The single writer used by
 * BOTH transports so their semantics cannot drift — the FSM in tasks/context
 * dedupes the double delivery by design ("if either arrives, the card settles").
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
    // Cancelled and timed-out are failures as far as the card is concerned;
    // the error string is what distinguishes them.
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
 * Live subagent events over the thread-scoped tasks WebSocket.
 *
 * Why WS when SSE already carries these: the custom stream dies with its RUN,
 * while todo bindings must survive reconnects and outlive it — the WS is
 * scoped to the thread and replays a bounded buffer on join. Delivery is
 * additive to the SSE path; the subtask FSM makes duplicate application a
 * no-op, so old frontends and new transports coexist.
 *
 * Reconnects with capped exponential backoff; the gateway may legitimately be
 * older than this build, and a failed upgrade must stay invisible.
 */
export function useThreadTaskEvents(
  threadId: string | null | undefined,
  updateSubtask: UpdateSubtask,
): void {
  useEffect(() => {
    if (!threadId) return;

    let closed = false;
    let socket: WebSocket | null = null;
    let retryTimer: ReturnType<typeof setTimeout> | undefined;
    let attempts = 0;

    // Same-origin deploys return an empty base — but the WebSocket
    // constructor REQUIRES an absolute URL, so resolve against the page
    // origin. Without this the socket never connects outside split-origin
    // setups, silently (the retry loop just spins).
    const base = getBackendBaseURL() || getBaseOrigin();
    const wsBase = base.replace(/^http/, "ws");
    const url = `${wsBase}/api/threads/${encodeURIComponent(threadId)}/tasks-ws`;

    const connect = () => {
      if (closed) return;
      try {
        socket = new WebSocket(url);
      } catch {
        scheduleRetry();
        return;
      }

      socket.onmessage = (message) => {
        try {
          const parsed: unknown = JSON.parse(String(message.data));
          if (isTaskLifecycleEvent(parsed)) {
            applyTaskEvent(parsed, updateSubtask);
          }
        } catch {
          /* non-JSON frame (heartbeat etc.) — ignore */
        }
      };

      socket.onopen = () => {
        attempts = 0;
      };

      socket.onclose = () => {
        socket = null;
        scheduleRetry();
      };
      socket.onerror = () => {
        // onclose follows; nothing to do here beyond preventing unhandled noise.
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
        // Detach handlers before close so onclose does not schedule a retry.
        socket.onclose = null;
        socket.onerror = null;
        socket.onmessage = null;
        socket.close();
        socket = null;
      }
    };
    // updateSubtask comes from context with stable identity; threadId scopes
    // the socket. Anything else changing should not rebuild the connection.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [threadId]);
}
