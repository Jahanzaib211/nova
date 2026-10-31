"use client";

import { useEffect, useRef, useState } from "react";

import { cn } from "@/lib/utils";

import {
  COMPUTER_PANEL_MAX_WIDTH,
  COMPUTER_PANEL_MIN_WIDTH,
} from "./fit-panel-width";
import { RESIZE_HANDLE_WIDTH, ResizeHandle } from "./resize-handle";
import { type PanelSide, usePanelWidth } from "./use-panel-width";

/**
 * A collapsible, resizable side column.
 *
 * Stays MOUNTED across open/close: closing animates the column to zero width
 * (so the neighbour reflows continuously) while the children keep their
 * state — for the Agent's Computer that is the live shell, browser history
 * and last tab. `invisible` keeps focus out of the collapsed surface.
 *
 * Width is the user's preference (persisted under `storageKey`) fitted to the
 * width of `containerRef` so the column it shares with the chat never leaves
 * the chat below its floor (see fitPanelWidth).
 */
export function SidePanel({
  open,
  side = "right",
  storageKey,
  defaultWidth = 640,
  min = COMPUTER_PANEL_MIN_WIDTH,
  max = COMPUTER_PANEL_MAX_WIDTH,
  containerRef,
  resizeLabel,
  className,
  children,
}: {
  open: boolean;
  side?: PanelSide;
  storageKey: string;
  defaultWidth?: number;
  min?: number;
  max?: number;
  /** The flex row the panel shares with its neighbour. */
  containerRef: React.RefObject<HTMLElement | null>;
  resizeLabel: string;
  className?: string;
  children: React.ReactNode;
}) {
  const containerWidth = useElementWidth(containerRef);
  const { width, dragging, handleProps } = usePanelWidth({
    side,
    storageKey,
    defaultWidth,
    min,
    max,
    containerWidth,
  });
  const handle = (
    <ResizeHandle
      handleProps={handleProps}
      label={resizeLabel}
      dragging={dragging}
      disabled={!open}
    />
  );
  return (
    <div
      aria-hidden={!open}
      data-side-panel={side}
      data-open={open}
      className={cn(
        "flex h-full shrink-0 overflow-hidden",
        // No width animation while dragging: it would lag the pointer.
        !dragging && "transition-[width] duration-300 ease-in-out",
        !open && "invisible",
        className,
      )}
      style={{ width: open ? width : 0 }}
    >
      {side === "right" && handle}
      {/* The column's width covers seam + content: the content div is fixed
          (not flex-1) so text does not reflow during the width transition,
          and it must subtract the seam or its last 8px are clipped. */}
      <div
        style={{ width: width - RESIZE_HANDLE_WIDTH }}
        className="bg-panel flex h-full shrink-0 flex-col overflow-hidden"
      >
        {children}
      </div>
      {side === "left" && handle}
    </div>
  );
}

/** Live content width of an element, via ResizeObserver; null until measured. */
export function useElementWidth(
  ref: React.RefObject<HTMLElement | null>,
): number | null {
  const [width, setWidth] = useState<number | null>(null);
  const last = useRef<number | null>(null);
  useEffect(() => {
    const el = ref.current;
    if (!el || typeof ResizeObserver === "undefined") return;
    const observer = new ResizeObserver(([entry]) => {
      const next = Math.round(entry?.contentRect.width ?? 0);
      if (next !== last.current) {
        last.current = next;
        setWidth(next);
      }
    });
    observer.observe(el);
    return () => observer.disconnect();
  }, [ref]);
  return width;
}
