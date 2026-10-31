"use client";

import { useCallback, useEffect, useRef, useState } from "react";

import { fitPanelWidth } from "./fit-panel-width";

export type PanelSide = "left" | "right";

export const KEYBOARD_STEP = 16;
export const KEYBOARD_STEP_LARGE = 64;

interface Bounds {
  min: number;
  max: number;
}

/** Width implied by a pointer drag that started at `startX` with `startWidth`. */
export function dragWidth(args: {
  side: PanelSide;
  startWidth: number;
  startX: number;
  clientX: number;
}): number {
  const delta = args.clientX - args.startX;
  return args.side === "right"
    ? args.startWidth - delta
    : args.startWidth + delta;
}

/** Width after a key press on the separator, or null when the key is not ours. */
export function keyboardWidth(
  width: number,
  side: PanelSide,
  key: string,
  shift: boolean,
  bounds?: Bounds,
): number | null {
  const step = shift ? KEYBOARD_STEP_LARGE : KEYBOARD_STEP;
  // Arrow pointing away from the panel's anchored edge grows it.
  const grow = side === "right" ? "ArrowLeft" : "ArrowRight";
  const shrink = side === "right" ? "ArrowRight" : "ArrowLeft";
  if (key === grow) return width + step;
  if (key === shrink) return width - step;
  if (key === "Home" && bounds) return bounds.min;
  if (key === "End" && bounds) return bounds.max;
  return null;
}

export function readStoredWidth(
  raw: string | null,
  opts: Bounds & { fallback: number },
): number {
  const n = Number(raw);
  return raw !== null && Number.isFinite(n) && n >= opts.min && n <= opts.max
    ? n
    : opts.fallback;
}

export interface UsePanelWidthOptions {
  side: PanelSide;
  storageKey: string;
  defaultWidth: number;
  min: number;
  max: number;
  /** Width of the area the panel shares with the chat; see fitPanelWidth. */
  containerWidth: number | null;
}

/**
 * Persistent, bounded panel width with pointer, keyboard and wheel resizing.
 *
 * The returned `handleProps` go on the separator element. Pointer capture
 * means the drag keeps tracking outside the handle (and works with touch —
 * the old mouse-only version could not be resized on a tablet at all).
 */
export function usePanelWidth(opts: UsePanelWidthOptions) {
  const { side, storageKey, defaultWidth, min, max, containerWidth } = opts;
  const [preferred, setPreferred] = useState<number>(() =>
    typeof window === "undefined"
      ? defaultWidth
      : readStoredWidth(safeGet(storageKey), {
          min,
          max,
          fallback: defaultWidth,
        }),
  );
  const clamp = useCallback(
    (w: number) => Math.min(max, Math.max(min, w)),
    [min, max],
  );
  const preferredRef = useRef(preferred);
  preferredRef.current = preferred;
  const [dragging, setDragging] = useState(false);

  const commit = useCallback(
    (w: number) => {
      const next = clamp(w);
      setPreferred(next);
      safeSet(storageKey, String(Math.round(next)));
    },
    [clamp, storageKey],
  );

  const onPointerDown = useCallback(
    (e: React.PointerEvent<HTMLElement>) => {
      if (e.button !== 0 && e.pointerType === "mouse") return;
      e.preventDefault();
      const target = e.currentTarget;
      target.setPointerCapture(e.pointerId);
      const startX = e.clientX;
      const startWidth = preferredRef.current;
      setDragging(true);
      const onMove = (ev: PointerEvent) =>
        setPreferred(
          clamp(dragWidth({ side, startWidth, startX, clientX: ev.clientX })),
        );
      const onUp = () => {
        setDragging(false);
        commit(preferredRef.current);
        target.removeEventListener("pointermove", onMove);
        target.removeEventListener("pointerup", onUp);
        target.removeEventListener("pointercancel", onUp);
      };
      target.addEventListener("pointermove", onMove);
      target.addEventListener("pointerup", onUp);
      target.addEventListener("pointercancel", onUp);
    },
    [clamp, commit, side],
  );

  const onKeyDown = useCallback(
    (e: React.KeyboardEvent<HTMLElement>) => {
      const next = keyboardWidth(
        preferredRef.current,
        side,
        e.key,
        e.shiftKey,
        { min, max },
      );
      if (next === null) return;
      e.preventDefault();
      commit(next);
    },
    [commit, side, min, max],
  );

  const onWheel = useCallback(
    (e: React.WheelEvent<HTMLElement>) => {
      commit(preferredRef.current - e.deltaY);
    },
    [commit],
  );

  useEffect(() => {
    if (!dragging) return;
    document.body.style.userSelect = "none";
    document.body.style.cursor = "col-resize";
    return () => {
      document.body.style.userSelect = "";
      document.body.style.cursor = "";
    };
  }, [dragging]);

  const width = fitPanelWidth(preferred, containerWidth);

  return {
    /** The width to render (yields to the chat's floor). */
    width,
    /** The user's preference, before fitting. */
    preferred,
    dragging,
    handleProps: {
      onPointerDown,
      onKeyDown,
      onWheel,
      role: "separator" as const,
      "aria-orientation": "vertical" as const,
      "aria-valuemin": min,
      "aria-valuemax": max,
      "aria-valuenow": Math.round(width),
      tabIndex: 0,
    },
  };
}

function safeGet(key: string): string | null {
  try {
    return localStorage.getItem(key);
  } catch {
    return null;
  }
}

function safeSet(key: string, value: string): void {
  try {
    localStorage.setItem(key, value);
  } catch {
    // Private mode / blocked storage: the width still works for the session.
  }
}
