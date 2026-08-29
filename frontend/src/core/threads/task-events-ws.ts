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

export type TodoSnapshotEvent = {
  thread_id: string;
  todos: Array<Record<string, unknown>>;
};
export type TerminalStatsEvent = { thread_id: string; total_commands: number };

export type ComputerEventHandlers = {
  /** Subagent lifecycle; applied to the subtask store via applyTaskEvent. */
  updateSubtask: UpdateSubtask;
  /** Dev-server transition - invalidate dev-status/dev-servers caches here. */
  onDevServer?: (event: DevServerEvent) => void;
  /** Workspace observation - invalidate file/preview caches here. */
  onObservation?: (event: WorkspaceObservationEvent) => void;
  /** Authoritative todo snapshot from the harness middleware. */
  onTodos?: (event: TodoSnapshotEvent) => void;
  /** Deterministic command total for the Terminal header. */
  onTerminalStats?: (event: TerminalStatsEvent) => void;
  /**
   * The session is gone and reconnecting cannot fix it. Fired once, after
   * retries have been abandoned, so the panel can tell the user to reload
   * instead of silently showing a dead socket forever.
   */
  onAuthExpired?: () => void;
};

/** Is this a subagent lifecycle event we can apply? */
export function isTaskLifecycleEvent(
  event: unknown,
): event is TaskLifecycleEvent {
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
          ...(e.description !== undefined
            ? { description: e.description }
            : {}),
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
/**
 * Consecutive failed connects before we spend a request asking whether the
 * session is still valid. Three covers the ordinary first-mount race (the
 * thread's metadata row lands a beat after the panel mounts) without letting a
 * genuinely dead session retry unnoticed for long.
 */
export const AUTH_PROBE_AFTER_ATTEMPTS = 3;

/** The socket URL for a thread. Exported so the shape stays pinned: nginx
 *  matches `^/api/threads/[^/]+/computer-ws$` exactly, and an earlier probe
 *  looked for a `/runs/{id}/` segment this path has never had. */
export function computerWsUrl(threadId: string, baseOverride?: string): string {
  const base = baseOverride ?? (getBackendBaseURL() || getBaseOrigin());
  return `${base.replace(/^http/, "ws")}/api/threads/${encodeURIComponent(
    threadId,
  )}/computer-ws`;
}

/**
 * What a failed handshake means, given what the REST probe said.
 *
 * A refused WebSocket handshake exposes no status to script, so a dead session
 * and a flaky network are indistinguishable at the socket. The probe separates
 * them, and this is the whole decision: 401 is terminal, everything else is
 * worth another attempt. 403/404 in particular is the benign first-mount race —
 * the panel connects before the thread's metadata row exists.
 */
export function authProbeVerdict(status: number): "expired" | "retry" {
  return status === 401 ? "expired" : "retry";
}

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
    const url = computerWsUrl(threadId, base);

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
        return;
      }
      if (
        typeof parsed === "object" &&
        parsed !== null &&
        (parsed as { channel?: string }).channel === "todos"
      ) {
        handlers.onTodos?.(parsed as TodoSnapshotEvent);
        return;
      }
      if (
        typeof parsed === "object" &&
        parsed !== null &&
        ((parsed as { type?: string }).type === "terminal_stats" ||
          (parsed as { channel?: string }).channel === "terminal_stats")
      ) {
        handlers.onTerminalStats?.(parsed as TerminalStatsEvent);
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

    // A failed WebSocket handshake exposes no status code to script, so a dead
    // session and a flaky network look identical from here — which is how a
    // stale tab ended up retrying a 403 every 18s for days with nothing shown
    // to the user. This endpoint separates them: 401 means the session itself
    // is gone, 403/404 means the thread is not owned *yet* (the panel mounts
    // before the first run creates it, so this is the benign startup race),
    // and anything else means the socket, not the credentials, is the problem.
    const sessionIsDead = async (): Promise<boolean> => {
      try {
        const resp = await fetch(
          `${base}/api/threads/${encodeURIComponent(threadId)}/runs`,
          { credentials: "include" },
        );
        return authProbeVerdict(resp.status) === "expired";
      } catch {
        return false; // offline: keep retrying, the credentials are fine
      }
    };

    const scheduleRetry = () => {
      if (closed) return;
      attempts += 1;
      // Only pay for the probe once the failures look persistent rather than
      // like the ordinary first-mount race.
      if (attempts === AUTH_PROBE_AFTER_ATTEMPTS) {
        void sessionIsDead().then((dead) => {
          if (closed || !dead) return;
          closed = true;
          clearTimeout(retryTimer);
          handlers.onAuthExpired?.();
        });
      }
      const delay = Math.min(15000, 1000 * 2 ** (attempts - 1));
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
