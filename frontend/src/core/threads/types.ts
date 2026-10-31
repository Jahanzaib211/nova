import type { Message, Thread } from "@langchain/langgraph-sdk";

import type { Todo } from "../todos";

export interface AgentThreadState extends Record<string, unknown> {
  title: string;
  messages: Message[];
  artifacts: string[];
  todos?: Todo[];
}

/** Per-chat runtime selection (P12). Kept as a named type because
 * `Omit<AgentThreadContext, …>` collapses over the index signature and
 * callers intersect this back in. */
export interface RuntimeContextFields {
  /** `native` or an acp_agents name (claude_code, openclaw). */
  runtime?: string;
  /** Account id for the runtime, or "auto". */
  runtime_account?: string;
  /** Permission preset for ACP runtimes. */
  permission_mode?: "full" | "standard" | "plan";
}

export interface AgentThreadContext extends Record<string, unknown> {
  thread_id: string;
  model_name: string | undefined;
  thinking_enabled: boolean;
  is_plan_mode: boolean;
  subagent_enabled: boolean;
  reasoning_effort?: "minimal" | "low" | "medium" | "high";
  agent_name?: string;
  runtime?: RuntimeContextFields["runtime"];
  runtime_account?: RuntimeContextFields["runtime_account"];
  permission_mode?: RuntimeContextFields["permission_mode"];
}

export interface AgentThread extends Thread<AgentThreadState> {
  context?: AgentThreadContext;
}

export interface RunMessage {
  run_id: string;
  seq?: number;
  content: Message;
  metadata: {
    caller: string;
  };
  created_at: string;
}

export interface ThreadTokenUsageResponse {
  thread_id: string;
  total_tokens: number;
  total_input_tokens: number;
  total_output_tokens: number;
  total_runs: number;
  by_model: Record<string, { tokens: number; runs: number }>;
  by_caller: {
    lead_agent: number;
    subagent: number;
    middleware: number;
  };
}
