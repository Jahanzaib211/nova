/**
 * How wide the Agent's Computer column may actually be.
 *
 * The saved preference (380–1100px) is honoured, but never at the chat's
 * expense: with the 640px default open inside a 1024px area, the chat column
 * was 384px wide on a 1280px laptop and a collapsed subtask row could not
 * even show its status. The column yields first; only when the area is too
 * small for both does the panel keep its own floor.
 */
export const COMPUTER_PANEL_MIN_WIDTH = 380;
export const COMPUTER_PANEL_MAX_WIDTH = 1100;
/** What the chat column keeps beside an open panel, when the area allows. */
export const CHAT_COLUMN_MIN_WIDTH = 520;

export function fitPanelWidth(
  preferred: number,
  containerWidth: number | null,
): number {
  const clamped = Math.min(
    COMPUTER_PANEL_MAX_WIDTH,
    Math.max(COMPUTER_PANEL_MIN_WIDTH, preferred),
  );
  if (containerWidth === null || containerWidth <= 0) return clamped;
  const available = containerWidth - CHAT_COLUMN_MIN_WIDTH;
  if (available >= clamped) return clamped;
  return Math.max(COMPUTER_PANEL_MIN_WIDTH, available);
}
