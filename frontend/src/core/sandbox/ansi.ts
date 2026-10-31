/**
 * Turn SGR escape sequences into styled spans for the Terminal.
 *
 * The backend deliberately keeps SGR (`ESC[…m`) and strips every other escape
 * (see `deerflow/utils/sanitize.py`), because colour is signal: a dev server's
 * red error line and green ready line are the fastest way to read a log, and a
 * terminal that renders them as plain text throws that away.
 *
 * This is not a terminal emulator and does not try to be. It handles the subset
 * a log stream actually uses — foreground/background colour, bold, dim,
 * italic, underline, and reset — and ignores the rest rather than guessing.
 * There is no cursor, no scrollback addressing, no line wrapping: those escapes
 * never reach here because the backend already dropped them.
 *
 * No dependency: `xterm.js` is ~250 KB for a full emulator we do not want, and
 * `ansi-to-html` builds an HTML string, which would mean `dangerouslySetInnerHTML`
 * on untrusted command output. Returning plain data that React renders as text
 * children keeps escaping the browser's job.
 */

/** One run of text sharing the same style. */
export interface AnsiSegment {
  text: string;
  /** Tailwind classes for this run, or "" for default terminal styling. */
  className: string;
}

// Matches only SGR: ESC [ <params> m. Everything else was stripped server-side,
// but a stray sequence from an older log must not become visible text either.
const SGR = /\x1b\[([0-9;]*)m/g;
// Any non-SGR escape that predates the backend sanitiser, so old rows render
// clean rather than showing raw bytes.
const OTHER_ESCAPE = /\x1b\[[0-?]*[ -/]*[@-~]|\x1b[@-Z\\-_]/g;

const FG: Record<number, string> = {
  30: "text-neutral-500",
  31: "text-red-400",
  32: "text-emerald-400",
  33: "text-amber-400",
  34: "text-blue-400",
  35: "text-fuchsia-400",
  36: "text-cyan-400",
  37: "text-neutral-200",
  90: "text-neutral-500",
  91: "text-red-300",
  92: "text-emerald-300",
  93: "text-amber-300",
  94: "text-blue-300",
  95: "text-fuchsia-300",
  96: "text-cyan-300",
  97: "text-white",
};

const BG: Record<number, string> = {
  41: "bg-red-500/20",
  42: "bg-emerald-500/20",
  43: "bg-amber-500/20",
  44: "bg-blue-500/20",
  45: "bg-fuchsia-500/20",
  46: "bg-cyan-500/20",
  47: "bg-neutral-500/20",
};

interface Style {
  fg?: string;
  bg?: string;
  bold?: boolean;
  dim?: boolean;
  italic?: boolean;
  underline?: boolean;
}

function classNameFor(style: Style): string {
  const parts: string[] = [];
  if (style.fg) parts.push(style.fg);
  if (style.bg) parts.push(style.bg);
  if (style.bold) parts.push("font-semibold");
  if (style.dim) parts.push("opacity-60");
  if (style.italic) parts.push("italic");
  if (style.underline) parts.push("underline");
  return parts.join(" ");
}

/** Apply one SGR parameter run to the running style. */
function applyParams(style: Style, params: string): Style {
  // A bare `ESC[m` is a reset, same as `ESC[0m`.
  const codes =
    params === "" ? [0] : params.split(";").map((n) => Number(n) || 0);
  let next: Style = { ...style };
  for (let i = 0; i < codes.length; i++) {
    const code = codes[i]!;
    if (code === 0) next = {};
    else if (code === 1) next.bold = true;
    else if (code === 2) next.dim = true;
    else if (code === 3) next.italic = true;
    else if (code === 4) next.underline = true;
    else if (code === 22) next = { ...next, bold: false, dim: false };
    else if (code === 23) next.italic = false;
    else if (code === 24) next.underline = false;
    else if (code === 39) next.fg = undefined;
    else if (code === 49) next.bg = undefined;
    else if (FG[code]) next.fg = FG[code];
    else if (BG[code]) next.bg = BG[code];
    // 256-colour and truecolour: consume their parameters so the colour index
    // is never mistaken for a style code, then fall back to default. Mapping
    // 256 shades onto a Tailwind palette would be invention, not fidelity.
    else if (code === 38 || code === 48) {
      const mode = codes[i + 1];
      i += mode === 5 ? 2 : mode === 2 ? 4 : 1;
    }
  }
  return next;
}

/**
 * Split text into styled segments. Always returns at least one segment for
 * non-empty input, so callers can render uniformly.
 */
export function parseAnsi(text: string): AnsiSegment[] {
  if (!text) return [];
  if (!text.includes("\x1b")) return [{ text, className: "" }];

  const segments: AnsiSegment[] = [];
  let style: Style = {};
  let cursor = 0;

  SGR.lastIndex = 0;
  let match: RegExpExecArray | null;
  while ((match = SGR.exec(text)) !== null) {
    if (match.index > cursor) {
      const chunk = text.slice(cursor, match.index).replace(OTHER_ESCAPE, "");
      if (chunk) segments.push({ text: chunk, className: classNameFor(style) });
    }
    style = applyParams(style, match[1] ?? "");
    cursor = match.index + match[0].length;
  }

  if (cursor < text.length) {
    const chunk = text.slice(cursor).replace(OTHER_ESCAPE, "");
    if (chunk) segments.push({ text: chunk, className: classNameFor(style) });
  }

  return segments;
}

/** Drop every escape, for copy-to-clipboard and plain-text callers. */
export function stripAnsi(text: string): string {
  if (!text?.includes("\x1b")) return text;
  return text.replace(SGR, "").replace(OTHER_ESCAPE, "");
}
