"use client";

import { CheckIcon, ChevronUpIcon, ListTodoIcon } from "lucide-react";
import { useCallback, useEffect, useId, useRef, useState } from "react";

import { useI18n } from "@/core/i18n/hooks";
import type { Todo } from "@/core/todos";
import { cn } from "@/lib/utils";

/** Row height and visible row count: the list shows whole rows, never a sliced one. */
const ROW_PX = 32;
const VISIBLE_ROWS = 4;
const LIST_PAD_PX = 4;

function StatusMark({ status }: { status: Todo["status"] }) {
  if (status === "completed") {
    return (
      <span className="bg-muted-foreground/15 text-muted-foreground/80 flex size-4 shrink-0 items-center justify-center rounded-full">
        <CheckIcon className="size-2.5" strokeWidth={3} />
      </span>
    );
  }
  if (status === "in_progress") {
    return (
      <span className="border-primary/60 relative flex size-4 shrink-0 items-center justify-center rounded-full border">
        <span className="bg-primary size-1.5 rounded-full motion-safe:animate-pulse" />
      </span>
    );
  }
  return (
    <span className="border-muted-foreground/35 size-4 shrink-0 rounded-full border" />
  );
}

/**
 * The plan strip that sits on the composer's top edge.
 *
 * Geometry: it is a tab attached to the composer, inset by exactly the
 * composer's corner radius (`rounded-2xl` = 16px, hence `mx-4`) so its edges
 * land on the composer's straight top border, with the same border token and
 * no bottom border -- one continuous outline. It is never translated: its box
 * ends exactly where the composer begins.
 *
 * Collapsed, it is only the header (with the step in progress), so there is no
 * strip for a half-row to show through. Expanded, the list is sized to whole
 * rows and fades at an edge only when there is more to scroll to.
 */
export function TodoList({
  className,
  todos,
  collapsed: controlledCollapsed,
  hidden = false,
  onToggle,
}: {
  className?: string;
  todos: Todo[];
  collapsed?: boolean;
  hidden?: boolean;
  onToggle?: () => void;
}) {
  const { t } = useI18n();
  const listId = useId();
  const [internalCollapsed, setInternalCollapsed] = useState(true);
  const isControlled = controlledCollapsed !== undefined;
  const collapsed = isControlled ? controlledCollapsed : internalCollapsed;

  const handleToggle = () => {
    if (isControlled) onToggle?.();
    else setInternalCollapsed((prev) => !prev);
  };

  const total = todos.length;
  const done = todos.filter((todo) => todo.status === "completed").length;
  const activeIndex = todos.findIndex((todo) => todo.status === "in_progress");
  const active = activeIndex >= 0 ? todos[activeIndex] : undefined;

  const listRef = useRef<HTMLOListElement>(null);
  const [edges, setEdges] = useState({ top: false, bottom: false });
  const measure = useCallback(() => {
    const el = listRef.current;
    if (!el) return;
    setEdges({
      top: el.scrollTop > 1,
      bottom: el.scrollTop + el.clientHeight < el.scrollHeight - 1,
    });
  }, []);

  // Keep the step in progress in view (scrolling the list only, never the page).
  useEffect(() => {
    const el = listRef.current;
    if (collapsed || !el) return;
    if (activeIndex >= 0) {
      const rowTop = LIST_PAD_PX + activeIndex * ROW_PX;
      if (rowTop < el.scrollTop || rowTop + ROW_PX > el.scrollTop + el.clientHeight) {
        el.scrollTop = Math.max(0, rowTop - ROW_PX);
      }
    }
    measure();
  }, [collapsed, activeIndex, total, measure]);

  const maskImage =
    edges.top && edges.bottom
      ? "linear-gradient(to bottom, transparent, black 14px, black calc(100% - 14px), transparent)"
      : edges.bottom
        ? "linear-gradient(to bottom, black calc(100% - 14px), transparent)"
        : edges.top
          ? "linear-gradient(to bottom, transparent, black 14px)"
          : undefined;

  return (
    <div
      className={cn(
        "bg-background border-input/50 mx-4 flex h-fit flex-col overflow-hidden rounded-t-xl border border-b-0 shadow-[0_-10px_24px_-18px_rgb(0_0_0/0.45)] transition-opacity duration-200 ease-out",
        hidden ? "pointer-events-none opacity-0" : "",
        className,
      )}
    >
      <button
        type="button"
        onClick={handleToggle}
        aria-expanded={!collapsed}
        aria-controls={listId}
        className="hover:bg-muted/40 group flex h-9 w-full shrink-0 items-center gap-2.5 px-3 text-left text-xs transition-colors"
      >
        <ListTodoIcon className="text-muted-foreground size-3.5 shrink-0" />
        <span className="text-foreground/90 shrink-0 font-medium">
          {t.a11y.todos}
        </span>
        <span className="text-muted-foreground/80 bg-muted/60 shrink-0 rounded-full px-1.5 py-px font-mono text-[10px] tabular-nums">
          {done}/{total}
        </span>
        <span className="text-muted-foreground min-w-0 flex-1 truncate">
          {collapsed ? active?.content : null}
        </span>
        <ChevronUpIcon
          className={cn(
            "text-muted-foreground group-hover:text-foreground size-3.5 shrink-0 transition-transform duration-200",
            collapsed ? "" : "rotate-180",
          )}
        />
      </button>

      {/* Progress, as the header's bottom rule: a full-width track, so the
          filled part reads as progress rather than a broken border. */}
      <div
        className="bg-muted mx-3 mb-1 h-0.5 shrink-0 overflow-hidden rounded-full"
        role="progressbar"
        aria-valuemin={0}
        aria-valuemax={total}
        aria-valuenow={done}
        aria-label={t.a11y.todos}
      >
        <div
          className="bg-success/80 h-full rounded-full transition-[width] duration-500 ease-out"
          style={{ width: total ? `${(done / total) * 100}%` : "0%" }}
        />
      </div>

      <div role="status" aria-live="polite" className="sr-only">
        {`${done}/${total} ${t.a11y.todos}${active?.content ? `, ${active.content}` : ""}`}
      </div>

      {!collapsed && (
        <ol
          id={listId}
          ref={listRef}
          onScroll={measure}
          className="overflow-y-auto overscroll-contain"
          style={{
            maxHeight: VISIBLE_ROWS * ROW_PX + LIST_PAD_PX * 2,
            paddingBlock: LIST_PAD_PX,
            maskImage,
            WebkitMaskImage: maskImage,
          }}
        >
          {todos.map((todo, i) => (
            <li
              key={`${i}:${todo.content ?? ""}`}
              title={todo.content}
              className={cn(
                "flex items-center gap-2.5 px-3 text-[13px]",
                todo.status === "in_progress" && "bg-primary/[0.06]",
              )}
              style={{ height: ROW_PX }}
            >
              <StatusMark status={todo.status} />
              <span
                className={cn(
                  "min-w-0 flex-1 truncate",
                  todo.status === "completed" &&
                    "text-muted-foreground/60 decoration-muted-foreground/40 line-through",
                  todo.status === "in_progress" && "text-foreground font-medium",
                  (todo.status === "pending" || !todo.status) &&
                    "text-muted-foreground",
                )}
              >
                {todo.content}
              </span>
            </li>
          ))}
        </ol>
      )}
    </div>
  );
}
