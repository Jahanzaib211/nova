/**
 * Which Agent's Computer tab an agent activity event belongs to, what kind of
 * work it represents, and which tab a running tool should focus.
 *
 * Three questions live here on purpose, because they were answered in five
 * places before and every one of them rotted independently:
 *
 *   1. Surface  (`isTerminalTool`/`isActivityTool`) — Terminal and Activity
 *      render a *partition* of the same event stream: Terminal shows
 *      command-line work (a command and its output), Activity shows
 *      everything else as high-signal cards. Every tool must land in exactly
 *      one. The failure mode is silent by construction: a misclassified event
 *      goes to the other tab rather than disappearing, so nothing errors.
 *
 *   2. Focus    (`isViewerTool`) — which tab a *running* tool should bring to
 *      the front. This is about user attention, not the partition: viewer
 *      tools are also Terminal-surface events (their output is logged like
 *      shell work), but while one is streaming the user wants the Editor.
 *
 *   3. Kind     (`classifyToolWork`) — the semantic flavor used for status
 *      labels, dots, icons and colors.
 */

/** Tools whose output reads as a terminal session. */
const TERMINAL_TOOL_NAMES: ReadonlySet<string> = new Set([
  // Command execution
  "bash",
  "execute_command",
  // Directory listing reads as a command transcript (`ls src/` → output).
  "ls",
  // Sandbox-native search tools; pattern hits read like a command transcript.
  "glob",
  "grep",
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
 * Tools whose *streaming* should put the Viewer tab in front. A superset of
 * nothing: these are all Terminal-surface events too (see above) — this set
 * only answers "which tab does the user want to watch right now".
 */
const EDITOR_FOCUS_TOOLS: ReadonlySet<string> = new Set([
  "write_file",
  "str_replace",
  "scaffold_project",
]);

/** What a tool does, for labels, dots, icons and colors. */
export type ToolWorkKind =
  | "terminal" // command execution and directory listings
  | "file-read"
  | "file-write"
  | "file-edit"
  | "file-search" // filename-oriented search
  | "content-search" // content-oriented search
  | "browser"
  | "devserver"
  | "subagent"
  | "scaffold"
  | "other";

const KIND_BY_NAME: ReadonlyMap<string, ToolWorkKind> = new Map([
  ["bash", "terminal"],
  ["execute_command", "terminal"],
  ["ls", "terminal"],
  ["free_port", "terminal"],
  ["system_probe", "terminal"],
  ["read_file", "file-read"],
  ["write_file", "file-write"],
  ["str_replace", "file-edit"],
  ["search_files", "file-search"],
  ["glob", "file-search"],
  ["grep_files", "content-search"],
  ["grep", "content-search"],
  ["start_dev_server", "devserver"],
  ["stop_dev_server", "devserver"],
  ["deploy_expose", "devserver"],
  ["task", "subagent"],
  ["scaffold_project", "scaffold"],
  ["screenshot", "browser"],
  ["view_image", "browser"],
  ["web_search", "browser"],
  ["web_fetch", "browser"],
  ["tavily_search", "browser"],
  ["image_search", "browser"],
]);

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

/** Which tab a running tool should focus, or null when no switch is due. */
export function isViewerTool(name: string): boolean {
  return typeof name === "string" && EDITOR_FOCUS_TOOLS.has(name);
}

/** Classify a tool's work for labels/dots/icons. Unknown names stay graceful. */
export function classifyToolWork(name: string): ToolWorkKind {
  if (typeof name !== "string") return "other";
  const known = KIND_BY_NAME.get(name);
  if (known) return known;
  if (TERMINAL_TOOL_PREFIXES.some((prefix) => name.startsWith(prefix)))
    return "terminal";
  if (
    name.startsWith("browser_") ||
    name.startsWith("web_") ||
    name.startsWith("tavily_") ||
    name.startsWith("image_")
  )
    return "browser";
  return "other";
}

/** Exported for tests that assert the partition is total and disjoint. */
export const TERMINAL_TOOL_NAMES_FOR_TEST = TERMINAL_TOOL_NAMES;
export const TERMINAL_TOOL_PREFIXES_FOR_TEST = TERMINAL_TOOL_PREFIXES;
