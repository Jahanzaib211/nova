"use client";

/**
 * The browser half of the voice session.
 *
 * Capture uses an AudioWorklet rather than MediaRecorder: we want raw samples,
 * not an Opus/WebM container. That removes a decode step on the server (ffmpeg
 * is never in the realtime path) and cuts latency.
 *
 * The worklet is loaded from a Blob URL so voice needs no separate static asset
 * and no build-tool wiring — one less thing to break in a deployment.
 */

import { useCallback, useEffect, useReducer, useRef, useState } from "react";

import { getBackendBaseURL } from "@/core/config";

import { CAPTURE_RATE, FRAME_SAMPLES, WORKLET_SOURCE } from "./capture";
import { VoicePlayer, toPcm16 } from "./playback";
import {
  initialVoiceState,
  voiceReducer,
  type VoiceEvent,
  type VoiceState,
} from "./state";

// Capture geometry and the worklet live in ./capture so the settings panel's
// microphone test drives the identical path. A test with its own copy would
// pass happily while the real conversation was broken.

export type UseVoiceSession = {
  state: VoiceState;
  /** Voice is configured and its engines loaded. */
  available: boolean;
  start: () => Promise<void>;
  stop: () => void;
  /** Send typed text through the voice channel; Nova still replies aloud. */
  say: (text: string) => void;
};

export function useVoiceSession(threadId: string | null): UseVoiceSession {
  const [state, dispatch] = useReducer(voiceReducer, initialVoiceState);
  const [available, setAvailable] = useState(false);

  const socketRef = useRef<WebSocket | null>(null);
  const playerRef = useRef<VoicePlayer | null>(null);
  const streamRef = useRef<MediaStream | null>(null);
  const ctxRef = useRef<AudioContext | null>(null);
  const workletUrlRef = useRef<string | null>(null);

  // Probe once so the mic button can be hidden rather than failing on click.
  useEffect(() => {
    let cancelled = false;
    void (async () => {
      try {
        const res = await fetch(`${getBackendBaseURL()}/api/voice/status`);
        if (!res.ok) return;
        const body = (await res.json()) as {
          enabled?: boolean;
          ready?: boolean;
        };
        if (!cancelled)
          setAvailable(Boolean(body.enabled && body.ready !== false));
      } catch {
        if (!cancelled) setAvailable(false);
      }
    })();
    return () => {
      cancelled = true;
    };
  }, []);

  const teardown = useCallback(() => {
    // Detach handlers and clear the ref BEFORE closing. `close()` fires
    // `onclose`, whose handler calls teardown again — with the ref still set
    // that re-enters and recurses. Browsers dispatch onclose asynchronously so
    // this is usually survivable in the wild, but it is a real hazard and it
    // deterministically hangs a synchronous test double.
    const socket = socketRef.current;
    socketRef.current = null;
    if (socket) {
      socket.onopen = null;
      socket.onmessage = null;
      socket.onerror = null;
      socket.onclose = null;
      try {
        socket.close();
      } catch {
        // Already closing; nothing to do.
      }
    }

    streamRef.current?.getTracks().forEach((t) => t.stop());
    streamRef.current = null;

    void ctxRef.current?.close().catch(() => undefined);
    ctxRef.current = null;

    void playerRef.current?.close();
    playerRef.current = null;

    if (workletUrlRef.current) {
      URL.revokeObjectURL(workletUrlRef.current);
      workletUrlRef.current = null;
    }
  }, []);

  const stop = useCallback(() => {
    try {
      socketRef.current?.send(JSON.stringify({ type: "stop" }));
    } catch {
      // Socket already gone; teardown still runs.
    }
    teardown();
    dispatch({ type: "closed" });
  }, [teardown]);

  const start = useCallback(async () => {
    if (!threadId || socketRef.current) return;
    dispatch({ type: "connecting" });

    try {
      const stream = await navigator.mediaDevices.getUserMedia({
        audio: {
          channelCount: 1,
          echoCancellation: true,
          noiseSuppression: true,
          // Without this the mic picks up Nova's own voice from the speakers
          // and the VAD interrupts her constantly — barge-in against herself.
          autoGainControl: true,
        },
      });
      streamRef.current = stream;

      const base = getBackendBaseURL();
      const origin = base
        ? base.replace(/^http/, "ws")
        : window.location.origin.replace(/^http/, "ws");
      const socket = new WebSocket(
        `${origin}/api/voice/session/${encodeURIComponent(threadId)}`,
      );
      socket.binaryType = "arraybuffer";
      socketRef.current = socket;

      const player = new VoicePlayer();
      playerRef.current = player;

      socket.onmessage = (ev: MessageEvent<string | ArrayBuffer>) => {
        if (typeof ev.data !== "string") {
          player.enqueue(ev.data);
          return;
        }
        let event: VoiceEvent;
        try {
          event = JSON.parse(ev.data) as VoiceEvent;
        } catch {
          return;
        }
        // Flush before reducing: the queued audio must stop the moment the
        // interrupt arrives, not after React re-renders.
        if (event.type === "interrupt") player.interrupt();
        dispatch(event);
      };

      socket.onerror = () =>
        dispatch({ type: "error", message: "voice connection failed" });
      socket.onclose = () => {
        teardown();
        dispatch({ type: "closed" });
      };

      await new Promise<void>((resolve, reject) => {
        socket.onopen = () => resolve();
        // Don't hang the UI forever on a silently-dropped handshake.
        setTimeout(() => reject(new Error("voice connect timed out")), 10_000);
      });

      const ctx = new AudioContext({ sampleRate: CAPTURE_RATE });
      ctxRef.current = ctx;
      const blobUrl = URL.createObjectURL(
        new Blob([WORKLET_SOURCE], { type: "application/javascript" }),
      );
      workletUrlRef.current = blobUrl;
      await ctx.audioWorklet.addModule(blobUrl);

      const source = ctx.createMediaStreamSource(stream);
      const node = new AudioWorkletNode(ctx, "nova-capture");

      let carry = new Float32Array(0);
      node.port.onmessage = (ev: MessageEvent<Float32Array>) => {
        if (socket.readyState !== WebSocket.OPEN) return;
        // The worklet's render quantum (128 samples) is smaller than a 20 ms
        // frame, so accumulate before sending — the server's VAD is frame-based
        // and short frames would be dropped.
        const merged = new Float32Array(carry.length + ev.data.length);
        merged.set(carry);
        merged.set(ev.data, carry.length);

        let offset = 0;
        while (merged.length - offset >= FRAME_SAMPLES) {
          const frame = merged.subarray(offset, offset + FRAME_SAMPLES);
          socket.send(toPcm16(frame, ctx.sampleRate, CAPTURE_RATE));
          offset += FRAME_SAMPLES;
        }
        carry = merged.slice(offset);
      };
      source.connect(node);
      // Not connected to destination: routing the mic to the speakers would
      // feed Nova's own output straight back into the VAD.
    } catch (e) {
      teardown();
      dispatch({
        type: "error",
        message: e instanceof Error ? e.message : "could not start voice",
      });
    }
  }, [threadId, teardown]);

  const say = useCallback((text: string) => {
    try {
      socketRef.current?.send(JSON.stringify({ type: "text", text }));
    } catch {
      // Nothing sensible to do; the UI already shows the socket state.
    }
  }, []);

  useEffect(() => teardown, [teardown]);

  return { state, available, start, stop, say };
}
