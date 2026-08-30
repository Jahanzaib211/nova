/**
 * Folding a command-total observation into the value the Terminal header shows.
 *
 * The gateway's counter (`terminal_stats.json`) is a **cumulative total**: it
 * only ever increases for a given thread. The frontend receives it from two
 * places that do not agree on recency:
 *
 *   - `GET /api/sandbox/terminal-stats` — a mount-time seed read off disk.
 *   - `terminal_stats` frames over `computer-ws` — the live updates.
 *
 * The socket's hub **replays a bounded buffer to every late joiner**, so a
 * reconnect re-delivers historical frames. Applying those unconditionally
 * rewound the header: a replayed `146` overwrote a live `313`, the next real
 * command snapped it back to `314`, and the number visibly flickered between
 * two values that were each individually correct. The seed loses the same race
 * from the other direction when it resolves after the first live frame.
 *
 * Monotonic folding makes replay idempotent and the two sources order-
 * independent, which is what the header needs: mount once with a number, then
 * only ever track the backend upward.
 */
export function foldCommandCount(
  current: number | undefined,
  incoming: number | null | undefined,
): number | undefined {
  // `null`/`undefined` mean "unknown", never zero — keep what we have rather
  // than dropping the header back to its approximate "~N" window count.
  if (typeof incoming !== "number" || !Number.isFinite(incoming)) return current;
  if (current === undefined) return incoming;
  return Math.max(current, incoming);
}
