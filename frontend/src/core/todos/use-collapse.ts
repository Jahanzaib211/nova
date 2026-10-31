"use client";

import { useCallback, useEffect, useState } from "react";

/**
 * Remembered collapse state for a thread's todo list.
 *
 * `TodoList` has accepted `collapsed`/`onToggle` since it was written, but both
 * chat pages passed neither -- so the only working control was the component's
 * private `useState`, which is per-mount and resets on every route change. The
 * user folded the list away, navigated, and it sprang open again.
 *
 * Session storage (not local) on purpose: this is a per-tab view preference
 * that should not outlive the browsing session, and it matches the key style
 * the Agent's Computer panel already uses for its per-thread preview path.
 */
export function useTodoCollapse(threadId: string | null | undefined) {
  const key = threadId ? `chat-todos-collapsed:${threadId}` : null;

  // Default collapsed: the composer sits directly below, and an auto-expanded
  // list pushes it down on every new run.
  const [collapsed, setCollapsed] = useState(true);

  // Read on mount and whenever the thread changes. Not a lazy initializer:
  // these pages keep the subtree mounted across thread switches, so an
  // initializer would strand the previous thread's value here.
  useEffect(() => {
    if (!key) return;
    try {
      setCollapsed(sessionStorage.getItem(key) !== "0");
    } catch {
      // Private mode / blocked site data -- fall back to the default.
      setCollapsed(true);
    }
  }, [key]);

  const toggle = useCallback(() => {
    setCollapsed((prev) => {
      const next = !prev;
      if (key) {
        try {
          sessionStorage.setItem(key, next ? "1" : "0");
        } catch {
          // Not being able to remember the choice must not break toggling it.
        }
      }
      return next;
    });
  }, [key]);

  return { collapsed, toggle };
}
