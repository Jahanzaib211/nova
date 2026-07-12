import {
  createContext,
  useCallback,
  useContext,
  useEffect,
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
  setTasks: (tasks: Record<string, Subtask>) => void;
}

export const SubtaskContext = createContext<SubtaskContextValue>({
  tasks: {},
  setTasks: () => {
    /* noop */
  },
});

export function SubtasksProvider({ children }: { children: React.ReactNode }) {
  const [tasks, setTasks] = useState<Record<string, Subtask>>({});
  return (
    <SubtaskContext.Provider value={{ tasks, setTasks }}>
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
  const { tasks, setTasks } = useSubtaskContext();
  const shouldNotifyAfterRenderRef = useRef(false);
  // Per-task authority of the stored status. Kept outside the Subtask shape
  // so the FSM's source rules don't leak into render props.
  const sourcesRef = useRef<Record<string, SubtaskUpdateSource>>({});
  // No deps: must run after every render to check the ref set during render.
  useEffect(() => {
    if (!shouldNotifyAfterRenderRef.current) {
      return;
    }
    shouldNotifyAfterRenderRef.current = false;
    setTasks({ ...tasks });
  });

  const updateSubtask = useCallback(
    (
      task: Partial<Subtask> & { id: string },
      source: SubtaskUpdateSource = "derived",
    ) => {
      const previous = tasks[task.id];
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

      const becameTerminal =
        isTerminalSubtaskStatus(next.status) && previousStatus !== next.status;

      tasks[task.id] = next;

      if (task.latestMessage) {
        setTasks({ ...tasks });
      } else if (becameTerminal) {
        shouldNotifyAfterRenderRef.current = true;
      }
    },
    [tasks, setTasks],
  );

  return updateSubtask;
}
