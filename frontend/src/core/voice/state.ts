/**
 * Voice session state machine — pure, no I/O.
 *
 * Kept separate from capture and playback so the turn-taking logic can be
 * tested without a browser, a microphone, or a WebSocket. Everything that
 * touches the world lives in `session.ts`; everything that decides *what
 * should happen* lives here.
 */

export type VoicePhase =
  | "idle" // connected, mic open, nobody talking
  | "connecting"
  | "listening" // you are talking
  | "thinking" // Nova is working on a reply
  | "speaking" // Nova is talking
  | "error"
  | "closed";

export type VoiceState = {
  phase: VoicePhase;
  /** Last final transcript of what you said. */
  transcript: string;
  /** Last assistant text Nova spoke. */
  assistant: string;
  /** Set when phase === "error". */
  error: string | null;
  /** True from the moment we ask the server to stop until playback is flushed. */
  interrupted: boolean;
  sampleRate: number;
};

export const initialVoiceState: VoiceState = {
  phase: "closed",
  transcript: "",
  assistant: "",
  error: null,
  interrupted: false,
  sampleRate: 16000,
};

/** Events from the server, plus the two the client raises locally. */
export type VoiceEvent =
  | { type: "connecting" }
  | { type: "ready"; sample_rate?: number }
  | { type: "listening" }
  | { type: "transcript"; text: string; final?: boolean }
  | { type: "thinking" }
  | { type: "assistant"; text: string }
  | { type: "speaking"; sample_rate?: number }
  | { type: "interrupt" }
  | { type: "idle" }
  | { type: "error"; message: string }
  | { type: "closed" };

export function voiceReducer(
  state: VoiceState,
  event: VoiceEvent,
): VoiceState {
  switch (event.type) {
    case "connecting":
      return { ...state, phase: "connecting", error: null };

    case "ready":
      return {
        ...state,
        phase: "idle",
        error: null,
        sampleRate: event.sample_rate ?? state.sampleRate,
      };

    case "listening":
      // Starting to talk always clears the interrupt latch: whatever Nova was
      // saying is gone, and this is a fresh turn.
      return { ...state, phase: "listening", interrupted: false };

    case "transcript":
      return { ...state, transcript: event.text };

    case "thinking":
      return { ...state, phase: "thinking" };

    case "assistant":
      // Replies arrive as one or more chunks; concatenate rather than replace
      // so a multi-sentence answer reads correctly in the UI.
      return {
        ...state,
        assistant: state.assistant ? `${state.assistant} ${event.text}` : event.text,
      };

    case "speaking":
      return {
        ...state,
        phase: "speaking",
        // A new turn starts a new assistant message.
        assistant: state.phase === "speaking" ? state.assistant : "",
      };

    case "interrupt":
      // Barge-in. The playback queue is flushed by the caller; here we only
      // record that it happened, and do NOT jump to idle — the user is
      // mid-utterance, so "listening" is about to arrive (or already has).
      return { ...state, interrupted: true };

    case "idle":
      return { ...state, phase: "idle", interrupted: false };

    case "error":
      return { ...state, phase: "error", error: event.message };

    case "closed":
      return { ...state, phase: "closed", interrupted: false };

    default:
      return state;
  }
}

/** Should the mic indicator be lit? */
export function isCapturing(state: VoiceState): boolean {
  return (
    state.phase === "idle" ||
    state.phase === "listening" ||
    state.phase === "thinking" ||
    state.phase === "speaking"
  );
}

/** Is the session usable (connected and not failed)? */
export function isLive(state: VoiceState): boolean {
  return state.phase !== "closed" && state.phase !== "error";
}

/** One-word label for the UI. */
export function phaseLabel(state: VoiceState): string {
  switch (state.phase) {
    case "connecting":
      return "Connecting";
    case "listening":
      return "Listening";
    case "thinking":
      return "Thinking";
    case "speaking":
      return "Speaking";
    case "error":
      return "Voice error";
    case "closed":
      return "Voice off";
    default:
      return "Ready";
  }
}
