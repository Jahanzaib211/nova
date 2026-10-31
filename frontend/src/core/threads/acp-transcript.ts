/**
 * Live transcript of an ACP agent (Claude Code, OpenClaw, …). Two things
 * produce it, both mirrored by the gateway as `acp_update` custom events
 * (`contracts/custom_events_contract.json`):
 *
 * - the `invoke_acp_agent` tool — the lead agent hands one task to an ACP
 *   agent; the transcript renders under that tool-call step;
 * - the chat *runtime* (`RuntimeDispatchMiddleware`) — the whole turn runs
 *   on the ACP agent and there is no tool call at all. The only visible
 *   output until the final message lands is this transcript, so it renders
 *   in place of the "is thinking" indicator (see `runtimeTranscripts`).
 *
 * State is keyed by agent name because one turn runs one session per agent.
 */

export interface AcpUpdateEvent {
  type: "acp_update";
  agent: string;
  session_id: string;
  kind: "text" | "status";
  delta: string;
}

export interface AcpTranscript {
  sessionId: string;
  text: string;
  /** Tool-call and permission notes, in order (bounded). */
  statuses: string[];
  /** Number of tool-call / permission events seen (not bounded like `statuses`). */
  actionCount: number;
}

export type AcpTranscripts = Record<string, AcpTranscript>;

const MAX_STATUSES = 50;
const MAX_TEXT = 20_000;

export function isAcpUpdateEvent(event: unknown): event is AcpUpdateEvent {
  if (typeof event !== "object" || event === null) return false;
  const e = event as Record<string, unknown>;
  return (
    e.type === "acp_update" &&
    typeof e.agent === "string" &&
    e.agent.length > 0 &&
    typeof e.session_id === "string" &&
    (e.kind === "text" || e.kind === "status") &&
    typeof e.delta === "string"
  );
}

/**
 * Fold one event in. A new session id for the same agent starts fresh text;
 * the status lines carry over, because the runtime announces itself
 * (`runtime: claude_code (plan)`) before the ACP session exists and that
 * line must not vanish the moment the real session id arrives.
 */
export function applyAcpUpdate(
  state: AcpTranscripts,
  event: AcpUpdateEvent,
): AcpTranscripts {
  const prev = state[event.agent];
  const base: AcpTranscript =
    prev?.sessionId === event.session_id
      ? prev
      : {
          sessionId: event.session_id,
          text: "",
          statuses: prev?.statuses ?? [],
          actionCount: prev?.actionCount ?? 0,
        };
  // A tool call between two text chunks means the next chunk is a new
  // paragraph, not a continuation ("…Agent's Computer.Let me explore…").
  const paragraphBreak =
    event.kind === "status" &&
    isActionStatus(event.delta) &&
    base.text.length > 0 &&
    !base.text.endsWith("\n")
      ? "\n\n"
      : "";
  const next: AcpTranscript =
    event.kind === "text"
      ? { ...base, text: (base.text + event.delta).slice(-MAX_TEXT) }
      : {
          ...base,
          text: base.text + paragraphBreak,
          statuses: [...base.statuses, event.delta].slice(-MAX_STATUSES),
          actionCount: base.actionCount + (isActionStatus(event.delta) ? 1 : 0),
        };
  return { ...state, [event.agent]: next };
}

/** Status lines that are an action of the agent, not narration about it. */
export function isActionStatus(line: string): boolean {
  return !line.startsWith("thinking:") && !line.startsWith("runtime");
}

/** Minimal message shape needed to find an `invoke_acp_agent` tool call. */
export interface AcpMessageLike {
  type?: string;
  tool_calls?: Array<{ name?: string; args?: unknown }> | null;
}

/**
 * Transcripts that belong to the chat runtime rather than to a tool call:
 * every agent with a transcript that no `invoke_acp_agent` call in the
 * current turn (after the last human message) is driving.
 */
export function runtimeTranscripts(
  transcripts: AcpTranscripts | undefined,
  messages: readonly AcpMessageLike[],
): Array<[string, AcpTranscript]> {
  if (!transcripts) return [];
  let start = 0;
  for (let i = messages.length - 1; i >= 0; i--) {
    if (messages[i]?.type === "human") {
      start = i;
      break;
    }
  }
  const toolDriven = new Set<string>();
  for (const message of messages.slice(start)) {
    if (message.type !== "ai") continue;
    for (const call of message.tool_calls ?? []) {
      if (call.name !== "invoke_acp_agent") continue;
      const args = call.args as { agent?: unknown } | undefined;
      if (typeof args?.agent === "string") toolDriven.add(args.agent);
    }
  }
  return Object.entries(transcripts).filter(
    ([agent, transcript]) =>
      !toolDriven.has(agent) &&
      (transcript.text.length > 0 || transcript.statuses.length > 0),
  );
}
