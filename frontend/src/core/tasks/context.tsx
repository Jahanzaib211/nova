import {
  createContext,
  type Dispatch,
  type MutableRefObject,
  type SetStateAction,
  useCallback,
  useContext,
  useRef,
  useState,
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

export interface SubtaskContextValue {
  tasks: Record<string, Subtask>;
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
  // A full Dispatch, not a value-only setter: writers must be able to use the
  // functional form so an update always applies to current state rather than
  // to whatever `tasks` their render captured.
  setTasks: Dispatch<SetStateAction<Record<string, Subtask>>>;
}

// Deliberately no default value. A concrete default made the `undefined` check
// in useSubtaskContext provably dead, so a consumer rendering outside the
// provider silently received an empty store and a no-op setter -- writes were
// accepted and dropped depending on mount order, which reads as flakiness
// rather than as the wiring error it is.
export const SubtaskContext = createContext<SubtaskContextValue | undefined>(
  undefined,
);

export function SubtasksProvider({ children }: { children: React.ReactNode }) {
  const [tasks, setTasks] = useState<Record<string, Subtask>>({});
  const sourcesRef = useRef<Record<string, SubtaskUpdateSource>>({});
  return (
    <SubtaskContext.Provider value={{ tasks, setTasks, sourcesRef }}>
      {children}
    </SubtaskContext.Provider>
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

export function useSubtask(id: string) {
  const { tasks } = useSubtaskContext();
  return tasks[id];
}

export function useUpdateSubtask() {
  const { setTasks, sourcesRef } = useSubtaskContext();

  const updateSubtask = useCallback(
    (
      task: Partial<Subtask> & { id: string },
      source: SubtaskUpdateSource = "derived",
    ) => {
      // Functional update, not a mutation of a captured `tasks` object.
      //
      // The previous version read and wrote `tasks` from the enclosing render
      // and memoised on `[tasks, setTasks]`, so any listener still holding an
      // earlier callback mutated an object React had already replaced -- the
      // write was accepted by the FSM and then silently lost. That is exactly
      // how a streamed `task_completed` landed and vanished before the next
      // event, leaving the card on its stale status. The updater form always
      // receives current state, so no writer can be stale.
      setTasks((current) => {
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
    [setTasks, sourcesRef],
  );

  return updateSubtask;
}
