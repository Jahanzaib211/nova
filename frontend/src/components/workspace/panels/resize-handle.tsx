"use client";

import { cn } from "@/lib/utils";

import type { usePanelWidth } from "./use-panel-width";

type HandleProps = ReturnType<typeof usePanelWidth>["handleProps"];

/** The seam's hit area (`w-2`), which the panel column must leave room for. */
export const RESIZE_HANDLE_WIDTH = 8;

/**
 * The draggable seam of a <SidePanel>.
 *
 * A 1px visible line inside an 8px hit area (the old handle was a 4px bar
 * that was both hard to grab and visually heavy). `touch-action: none` lets
 * the pointer drag work on touch screens instead of scrolling the page.
 */
export function ResizeHandle({
  handleProps,
  label,
  dragging,
  disabled,
  className,
}: {
  handleProps: HandleProps;
  label: string;
  dragging: boolean;
  disabled?: boolean;
  className?: string;
}) {
  return (
    <div
      {...handleProps}
      title={label}
      aria-label={label}
      aria-disabled={disabled ? true : undefined}
      className={cn(
        "group/handle relative w-2 shrink-0 cursor-col-resize touch-none outline-none select-none",
        "focus-visible:bg-ring/20",
        disabled && "pointer-events-none",
        className,
      )}
    >
      <div
        aria-hidden
        className={cn(
          "bg-panel-border absolute inset-y-0 left-1/2 w-px -translate-x-1/2 transition-colors",
          "group-hover/handle:bg-ring/60 group-focus-visible/handle:bg-ring",
          dragging && "bg-ring",
        )}
      />
    </div>
  );
}
