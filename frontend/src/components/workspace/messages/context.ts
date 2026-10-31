import type { BaseStream } from "@langchain/langgraph-sdk/react";
import { createContext, useContext } from "react";

import type { AgentThreadState } from "@/core/threads";
import type { AcpTranscripts } from "@/core/threads/acp-transcript";
import type { AgentActivityEvent } from "@/core/threads/hooks";

export interface TaskProgress {
  step: number;
  total: number;
  status: string;
}

export interface VerifyResultRoute {
  route: string;
  ok: boolean;
  status: number | null;
  notes: string;
}

export interface VerifyResult {
  thread_id: string;
  ok: boolean;
  verdict: "passed" | "issues";
  routes: VerifyResultRoute[];
  console_errors_count: number;
  screenshot: string | null;
}

export interface LlmError {
  error_type: string;
  reason: string;
  detail: string;
  http_status: number | null;
  code: string | null;
}

export interface ThreadContextType {
  thread: BaseStream<AgentThreadState>;
  isMock?: boolean;
  currentTool: string | null;
  taskProgress: TaskProgress | null;
  verifyResult: VerifyResult | null;
  llmError: LlmError | null;
  activityEvents: AgentActivityEvent[];
  activeWriteFilePath: string | null;
  /** Streamed output of ACP agents, keyed by agent name (see acp-transcript.ts). */
  acpTranscripts?: AcpTranscripts;
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
