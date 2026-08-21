/**
 * Which Agent's Computer tab an agent activity event belongs to.
 *
 * The Terminal and Activity tabs render a *partition* of the same event stream:
 * Terminal shows command-line work (a command and its output), Activity shows
 * everything else as high-signal cards. Every tool must land in exactly one.
 *
 * This lived as a bare name list inside terminal-tab.tsx, and it drifted twice:
 *
 *   2026-08-14  `write_file` / `str_replace` / `read_file` were missing, so a
 *               run that only edited files left the Terminal tab empty.
 *   2026-08-21  the entire `shell_*` family was missing. Those tools wrap the
 *               AIO SDK and are the modern execution path, so an agent doing
 *               its work through `shell_session` showed *nothing* in Terminal
 *               while the backend stream was perfectly healthy.
 *
 * A hand-maintained list of names rots every time a tool is added, and the
 * failure is silent — the event goes to the other tab rather than disappearing,
 * so nothing errors and nobody notices until a user says "the terminal is
 * broken". Hence the prefix rule: a new `shell_*` tool is classified correctly
 * the day it is added, without anyone remembering this file exists.
 */

/** Tools whose output reads as a terminal session. */
const TERMINAL_TOOL_NAMES: ReadonlySet<string> = new Set([
  // Command execution
  "bash",
  "execute_command",
  // File operations the agent narrates like shell work
  "read_file",
  "write_file",
  "str_replace",
  "search_files",
  "grep_files",
]);

/**
 * Name prefixes that are always terminal work.
 *
 * `shell_session` / `shell_view` / `shell_wait` / `shell_write` / `shell_kill`
 * are a family, and families grow. Matching the prefix means the next one is
 * handled without a code change here.
 */
const TERMINAL_TOOL_PREFIXES: readonly string[] = ["shell_"];

/**
 * `name` is typed `string`, but this classifies events decoded from a live
 * network stream, and a streaming tool call carries no `name` until enough
 * deltas have arrived — the same partial-tool-call window that broke the hook
 * order in `message-group.tsx`. TypeScript cannot enforce a type across that
 * boundary, so treat anything non-string as "not a terminal tool" and let the
 * event land in Activity until the name resolves.
 *
 * The list this replaced was consulted as `TERMINAL_TOOLS.has(e.type)`, and
 * `Set.has(undefined)` is simply `false`. Adding the prefix rule turned that
 * silent no-op into `undefined.startsWith(...)`, which throws and takes out the
 * whole Agent's Computer subtree via its error boundary. Hence the guard.
 */
export function isTerminalTool(name: string): boolean {
  if (typeof name !== "string") return false;
  if (TERMINAL_TOOL_NAMES.has(name)) return true;
  return TERMINAL_TOOL_PREFIXES.some((prefix) => name.startsWith(prefix));
}

/**
 * The complement. Written as its own function rather than `!isTerminalTool` at
 * each call site so the partition is stated once and cannot be inverted by
 * accident in one of the two tabs.
 */
export function isActivityTool(name: string): boolean {
  return !isTerminalTool(name);
}

/** Exported for tests that assert the partition is total and disjoint. */
export const TERMINAL_TOOL_NAMES_FOR_TEST = TERMINAL_TOOL_NAMES;
export const TERMINAL_TOOL_PREFIXES_FOR_TEST = TERMINAL_TOOL_PREFIXES;
