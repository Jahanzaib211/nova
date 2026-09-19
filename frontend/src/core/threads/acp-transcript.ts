/**
 * Live transcript of an ACP agent (Claude Code, OpenClaw, …) invoked through
 * `invoke_acp_agent`. The gateway mirrors every text chunk and every
 * permission/tool-call status as an `acp_update` custom event
 * (`contracts/custom_events_contract.json`); this module is the pure state
 * for it, keyed by agent name because one tool call runs one agent session.
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

/** Fold one event in. A new session id for the same agent starts a fresh transcript. */
export function applyAcpUpdate(
  state: AcpTranscripts,
  event: AcpUpdateEvent,
): AcpTranscripts {
  const prev = state[event.agent];
  const base: AcpTranscript =
    prev?.sessionId === event.session_id
      ? prev
      : { sessionId: event.session_id, text: "", statuses: [] };
  const next: AcpTranscript =
    event.kind === "text"
      ? { ...base, text: (base.text + event.delta).slice(-MAX_TEXT) }
      : {
          ...base,
          statuses: [...base.statuses, event.delta].slice(-MAX_STATUSES),
        };
  return { ...state, [event.agent]: next };
}
