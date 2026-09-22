import {
  createContext,
  type MutableRefObject,
  useCallback,
  useContext,
  useMemo,
  useRef,
  useSyncExternalStore,
} from "react";

import type { Subtask } from "./types";

function isTerminalSubtaskStatus(status: Subtask["status"] | undefined) {
  return status === "completed" || status === "failed";
}

/**
 * Who asserts a status update:
 * - "result": parsed from an actual ToolMessage — the backend's single
 *   terminal state for the task. Authoritative.
 * - "derived": inferred from stream/run liveness (no ToolMessage yet).
 *   A guess; must never overwrite a terminal state.
 */
export type SubtaskUpdateSource = "derived" | "result";

/**
 * Explicit finite state machine for subtask status.
 *
 * in_progress → completed   (any source)
 * in_progress → failed      (any source)
 * completed / failed        terminal for same-or-lower authority
 * derived terminal → result terminal   (a real ToolMessage corrects a guess)
 *
 * Everything else is an invalid transition and is ignored — the previous
 * status wins. Returns the status to store.
 */
export function nextSubtaskStatus(
  previous: Subtask["status"] | undefined,
  incoming: Subtask["status"] | undefined,
  previousSource: SubtaskUpdateSource | undefined,
  incomingSource: SubtaskUpdateSource,
): { status: Subtask["status"] | undefined; accepted: boolean } {
  if (incoming === undefined || incoming === previous) {
    return { status: previous, accepted: incoming !== undefined };
  }
  // A guess may never overwrite something the backend actually reported, even
  // when the reported state is non-terminal. Without this, a streamed
  // `in_progress` from task_running was still clobbered by the derived
  // "no active run -> failed" pass, which is precisely how a healthy subagent
  // got painted red while its messages were still arriving.
  if (incomingSource === "derived" && previousSource === "result") {
    return { status: previous, accepted: false };
  }
  if (!isTerminalSubtaskStatus(previous)) {
    return { status: incoming, accepted: true };
  }
  // Previous is terminal: only a result-sourced update may correct a
  // derived guess (e.g. derived "failed" after a dropped stream, then the
  // real ToolMessage replays as "completed"). Result-sourced terminal
  // states are immutable.
  if (incomingSource === "result" && previousSource !== "result") {
    return { status: incoming, accepted: true };
  }
  return { status: previous, accepted: false };
}

const SUBTASK_DEBUG =
  typeof window !== "undefined" &&
  (process.env.NODE_ENV !== "production" ||
    window.localStorage?.getItem("nova:subtask-debug") === "1");

let subtaskEventSeq = 0;

function logSubtaskTransition(entry: {
  id: string;
  previous: Subtask["status"] | undefined;
  incoming: Subtask["status"] | undefined;
  stored: Subtask["status"] | undefined;
  source: SubtaskUpdateSource;
  accepted: boolean;
}) {
  if (!SUBTASK_DEBUG) return;

  console.debug("[subtask-fsm]", {
    ts: new Date().toISOString(),
    seq: ++subtaskEventSeq,
    ...entry,
  });
}

type SubtaskUpdater = (
  current: Record<string, Subtask>,
) => Record<string, Subtask>;

/**
 * The subtask registry as an external store.
 *
 * It used to be `useState` inside the provider, with writers using the
 * functional form. Under load (2026-09-18, a thread with an orphaned `task`
 * call) React kept re-applying the queued updater against a stale base while
 * the surrounding render was restarted, so the same transition was "accepted"
 * on every render and never converged — React #185, and the route boundary
 * replaced the whole workspace. A store applies each write synchronously to
 * the *actual* current state, notifies only when the reference changed, and
 * hands React a cached snapshot, so there is no update queue to replay.
 */
export class SubtaskStore {
  private tasks: Record<string, Subtask> = {};
  private readonly listeners = new Set<() => void>();

  getSnapshot = (): Record<string, Subtask> => this.tasks;

  subscribe = (listener: () => void): (() => void) => {
    this.listeners.add(listener);
    return () => {
      this.listeners.delete(listener);
    };
  };

  /** Apply `updater` to the live state; notify only if the reference changed. */
  update(updater: SubtaskUpdater): void {
    const next = updater(this.tasks);
    if (next === this.tasks) return;
    this.tasks = next;
    for (const listener of this.listeners) listener();
  }
}

export interface SubtaskContextValue {
  store: SubtaskStore;
  /**
   * Per-task authority of the stored status, shared by every writer.
   *
   * This lives on the context, not inside useUpdateSubtask, because the hook
   * has more than one caller: the stream in useThreadStream and the derived
   * pass in MessageList. A ref per hook instance meant each writer kept its own
   * idea of who last wrote, so MessageList never saw that the stream had
   * recorded a "result" status and happily overwrote it with a guess -- the
   * FSM's authority rule was silently only half in force.
   */
  sourcesRef: MutableRefObject<Record<string, SubtaskUpdateSource>>;
}

// Deliberately no default value. A concrete default made the `undefined` check
// in useSubtaskContext provably dead, so a consumer rendering outside the
// provider silently received an empty store and a no-op setter -- writes were
// accepted and dropped depending on mount order, which reads as flakiness
// rather than as the wiring error it is.
export const SubtaskContext = createContext<SubtaskContextValue | undefined>(
  undefined,
);

const EMPTY_TASKS: Record<string, Subtask> = {};

export function SubtasksProvider({ children }: { children: React.ReactNode }) {
  const storeRef = useRef<SubtaskStore | null>(null);
  storeRef.current ??= new SubtaskStore();
  const sourcesRef = useRef<Record<string, SubtaskUpdateSource>>({});
  const value = useMemo(() => ({ store: storeRef.current!, sourcesRef }), []);
  return (
    <SubtaskContext.Provider value={value}>{children}</SubtaskContext.Provider>
  );
}

export function useSubtaskContext() {
  const context = useContext(SubtaskContext);
  if (context === undefined) {
    throw new Error(
      "useSubtaskContext must be used within a SubtaskContext.Provider",
    );
  }
  return context;
}

/** The live registry, subscribed through useSyncExternalStore. */
export function useSubtasks(): Record<string, Subtask> {
  const { store } = useSubtaskContext();
  return useSyncExternalStore(
    store.subscribe,
    store.getSnapshot,
    () => EMPTY_TASKS,
  );
}

export function useSubtask(id: string) {
  return useSubtasks()[id];
}

/**
 * Todo indexes that a COMPLETED subagent settled, unioned across all tasks.
 *
 * This replaces the old positional heuristic (first N rows struck by count of
 * done tasks), which struck the wrong rows whenever subagents finished out of
 * order. Indexes arrive from the backend's task events (todo_indexes); rows
 * with no binding are left exactly as the agent's own todo status says.
 */
export function completedTodoIndexes(
  tasks: Record<string, Subtask>,
): Set<number> {
  const bindings = new Set<number>();
  for (const task of Object.values(tasks)) {
    if (task.status !== "completed") continue;
    for (const index of task.todoIndexes ?? []) bindings.add(index);
  }
  return bindings;
}

export function useCompletedTodoBindings(): Set<number> {
  const tasks = useSubtasks();
  const signature = Object.values(tasks)
    .map((t) => `${t.id}:${t.status}:${(t.todoIndexes ?? []).join(",")}`)
    .sort()
    .join("|");
  return useMemo(() => completedTodoIndexes(tasks), [signature]); // eslint-disable-line react-hooks/exhaustive-deps
}

/**
 * Settle every still-in_progress subtask as superseded.
 *
 * Called on the rising edge of a NEW run: subagents from the previous run can
 * never complete now, and leaving them spinning produced ghost "running"
 * boxes that stacked across retries. Result-sourced failed status settles
 * them authoritatively.
 */
/**
 * How long a subtask is protected from being superseded after first sight.
 *
 * Covers the gap between the socket delivering `task_started` and the message
 * stream carrying the matching tool_call. Generous enough to absorb a slow
 * hydration, short enough that a genuinely orphaned subtask still settles
 * rather than spinning forever.
 */
export const SUPERSEDE_GRACE_MS = 10_000;

export function useSupersedeStaleSubtasks() {
  const { store } = useSubtaskContext();
  /**
   * Settle only subtasks that are NOT part of the CURRENT run.
   *
   * Membership is decided by the live tool_call ids in `messages` — the same
   * stream that created them. A naive "rising isLoading edge" version killed
   * genuinely-running subagents on page refresh (the run was still live, the
   * edge just fired on hydration), which read as a wall of red failures.
   */
  return useCallback(
    (currentToolCallIds: Set<string>) => {
      store.update((current) => {
        let changed = false;
        const next: Record<string, Subtask> = {};
        const now = Date.now();
        for (const [id, task] of Object.entries(current)) {
          const isLive = currentToolCallIds.has(id);
          // A subtask younger than the grace window has not had time to appear
          // in `messages` yet — the socket delivers task_started ahead of the
          // assistant message that carries its tool_call. Superseding it here
          // would fail a subagent that had only just started.
          const tooYoung =
            task.firstSeenAt !== undefined &&
            now - task.firstSeenAt < SUPERSEDE_GRACE_MS;
          if (task.status === "in_progress" && !isLive && !tooYoung) {
            changed = true;
            next[id] = {
              ...task,
              status: "failed",
              error: task.error ?? "superseded by a newer run",
            };
          } else {
            next[id] = task;
          }
        }
        return changed ? next : current;
      });
    },
    [store],
  );
}

/** The fields `updateSubtask` treats as observable when deciding identity. */
const OBSERVABLE_SUBTASK_FIELDS = [
  "status",
  "result",
  "error",
  "latestMessage",
  "description",
  "subagent_type",
  "prompt",
] as const;

/**
 * True when a write carries nothing the rendered task does not already show.
 * MessageList's derived pass queues a write per task on every render; skipping
 * the ones the store already reflects keeps the flush from ever re-issuing a
 * transition it has already made.
 */
export function subtaskWriteIsNoop(
  existing: Subtask | undefined,
  write: Partial<Subtask> & { id: string },
): boolean {
  if (existing === undefined) return false;
  for (const field of OBSERVABLE_SUBTASK_FIELDS) {
    if (field in write && write[field] !== existing[field]) return false;
  }
  return true;
}

export function useUpdateSubtask() {
  const { store, sourcesRef } = useSubtaskContext();

  const updateSubtask = useCallback(
    (
      task: Partial<Subtask> & { id: string },
      source: SubtaskUpdateSource = "derived",
    ) => {
      // Applied to the store's live state, never to a `tasks` object captured
      // by some render: a listener holding an earlier callback used to mutate
      // an object React had already replaced, so a streamed `task_completed`
      // was accepted by the FSM and then silently lost.
      store.update((current) => {
        const previous = current[task.id];
        const previousStatus = previous?.status;
        const previousSource = sourcesRef.current[task.id];

        const { status: storedStatus, accepted } = nextSubtaskStatus(
          previousStatus,
          task.status,
          previousSource,
          source,
        );

        logSubtaskTransition({
          id: task.id,
          previous: previousStatus,
          incoming: task.status,
          stored: storedStatus,
          source,
          accepted,
        });

        const next = {
          ...previous,
          ...task,
          ...(storedStatus !== undefined ? { status: storedStatus } : {}),
          // Stamped once, on first sight. Every writer funnels through here, so
          // this is the only place that can know which write was the first.
          firstSeenAt: previous?.firstSeenAt ?? Date.now(),
        } as Subtask;
        // A rejected status update must not smuggle in its result/error either
        // (e.g. a derived "failed" placeholder error overwriting a real result).
        if (!accepted && task.status !== undefined) {
          if (previous?.result !== undefined) next.result = previous.result;
          if (previous?.error !== undefined) next.error = previous.error;
        }

        if (accepted && task.status !== undefined) {
          sourcesRef.current[task.id] = source;
        }

        // Identity matters: returning a fresh object every call re-renders,
        // which makes MessageList's derived pass call straight back in — an
        // unbounded render loop. Hand back the same reference when nothing
        // observable changed so React bails out instead.
        // Note `previous` really can be undefined at runtime: indexing a
        // Record<string, Subtask> is not `| undefined` without
        // noUncheckedIndexedAccess, so TypeScript will not catch a bare
        // dereference here.
        if (previous === undefined) {
          return { ...current, [task.id]: next };
        }

        const unchanged =
          next.status === previous.status &&
          next.result === previous.result &&
          next.error === previous.error &&
          next.latestMessage === previous.latestMessage &&
          next.description === previous.description &&
          next.subagent_type === previous.subagent_type &&
          next.prompt === previous.prompt;

        return unchanged ? current : { ...current, [task.id]: next };
      });
    },
    [store, sourcesRef],
  );

  return updateSubtask;
}
