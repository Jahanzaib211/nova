import type { BaseStream } from "@langchain/langgraph-sdk/react";
import { createContext, useContext } from "react";

import type { AgentThreadState } from "@/core/threads";
import type { AgentActivityEvent } from "@/core/threads/hooks";

export interface TaskProgress {
  step: number;
  total: number;
  status: string;
}

export interface ThreadContextType {
  thread: BaseStream<AgentThreadState>;
  isMock?: boolean;
  currentTool: string | null;
  taskProgress: TaskProgress | null;
  activityEvents: AgentActivityEvent[];
  activeWriteFilePath: string | null;
  onAgentMessage?: (text: string) => void;
}

export const ThreadContext = createContext<ThreadContextType | undefined>(
  undefined,
);

export function useThread() {
  const context = useContext(ThreadContext);
  if (context === undefined) {
    throw new Error("useThread must be used within a ThreadContext");
  }
  return context;
}
