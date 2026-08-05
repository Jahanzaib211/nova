/**
 * Summarizers for the backend `trading` tool group.
 *
 * The tools (`get_ohlcv`, `compute_indicators`, `backtest_signals`) return a
 * JSON string. Without a summarizer the chat would either dump hundreds of
 * candles inline or show nothing but the tool name, so this reduces each
 * payload to the handful of numbers a decision actually turns on.
 *
 * Kept as pure functions separate from the renderer so the parsing is unit
 * testable — every input here arrives from the network and may be truncated,
 * an error string, or malformed.
 */

export type StatTone = "positive" | "negative" | "neutral";

export interface TradingStat {
  label: string;
  value: string;
  tone?: StatTone;
}

export interface TradingSummary {
  /** Headline, e.g. "GC=F · 15m · yfinance". */
  subtitle?: string;
  stats: TradingStat[];
  /** Set when the tool returned an `Error: ...` string instead of JSON. */
  error?: string;
}

export const TRADING_TOOL_NAMES = [
  "get_ohlcv",
  "compute_indicators",
  "backtest_signals",
] as const;

export type TradingToolName = (typeof TRADING_TOOL_NAMES)[number];

export function isTradingTool(name: string): name is TradingToolName {
  return (TRADING_TOOL_NAMES as readonly string[]).includes(name);
}

/** Trim a number for display without lying about magnitude. */
function fmt(value: unknown, digits = 2): string | undefined {
  if (typeof value !== "number" || !Number.isFinite(value)) {
    return undefined;
  }
  // Prices need more precision than ratios; keep small values readable.
  if (Math.abs(value) > 0 && Math.abs(value) < 0.01) {
    return value.toPrecision(2);
  }
  return value.toLocaleString(undefined, {
    minimumFractionDigits: 0,
    maximumFractionDigits: digits,
  });
}

function str(value: unknown): string | undefined {
  return typeof value === "string" && value.length > 0 ? value : undefined;
}

/** ISO8601 -> "Jul 31, 19:00" (UTC preserved; charts are UTC-anchored). */
function fmtTime(value: unknown): string | undefined {
  const raw = str(value);
  if (!raw) return undefined;
  const date = new Date(raw);
  if (Number.isNaN(date.getTime())) return raw;
  return date.toISOString().slice(0, 16).replace("T", " ") + " UTC";
}

function parsePayload(
  result: string | undefined,
): { error?: string; data?: Record<string, unknown> } | null {
  if (typeof result !== "string" || result.length === 0) {
    return null;
  }
  const trimmed = result.trim();
  if (trimmed.startsWith("Error:")) {
    return { error: trimmed.slice("Error:".length).trim() };
  }
  if (!trimmed.startsWith("{")) {
    // A streaming result can arrive partial; render nothing rather than
    // showing a broken half-parse.
    return null;
  }
  try {
    const parsed: unknown = JSON.parse(trimmed);
    if (typeof parsed !== "object" || parsed === null || Array.isArray(parsed)) {
      return null;
    }
    return { data: parsed as Record<string, unknown> };
  } catch {
    return null;
  }
}

function contextSubtitle(data: Record<string, unknown>): string | undefined {
  const parts = [
    str(data.symbol),
    str(data.interval),
    str(data.source),
  ].filter((part): part is string => Boolean(part));
  return parts.length > 0 ? parts.join(" · ") : undefined;
}

function summarizeOhlcv(data: Record<string, unknown>): TradingSummary {
  const stats: TradingStat[] = [];
  const count = data.count;
  if (typeof count === "number") {
    stats.push({ label: "bars", value: String(count) });
  }

  const latest = data.latest;
  if (typeof latest === "object" && latest !== null) {
    const bar = latest as Record<string, unknown>;
    const close = fmt(bar.close, 4);
    if (close) stats.push({ label: "last", value: close });
    const time = fmtTime(bar.time);
    if (time) stats.push({ label: "at", value: time });
  }

  return { subtitle: contextSubtitle(data), stats };
}

/**
 * Indicator series come back as tails (arrays); the latest value is the one
 * worth surfacing. Ordering is fixed rather than object-key order so the
 * display doesn't reshuffle between turns.
 */
const INDICATOR_DISPLAY_ORDER = [
  "ema_9",
  "ema_21",
  "ema_50",
  "sma_20",
  "sma_50",
  "rsi_14",
  "atr_14",
  "adx_adx",
  "macd_macd",
  "bb_upper",
  "bb_lower",
  "vwap",
];

const MAX_INDICATOR_STATS = 6;

function latestOf(series: unknown): number | undefined {
  if (!Array.isArray(series)) return undefined;
  for (let i = series.length - 1; i >= 0; i--) {
    const value: unknown = series[i];
    if (typeof value === "number" && Number.isFinite(value)) {
      return value;
    }
  }
  return undefined;
}

function summarizeIndicators(data: Record<string, unknown>): TradingSummary {
  const stats: TradingStat[] = [];
  const lastClose = fmt(data.last_close, 4);
  if (lastClose) stats.push({ label: "close", value: lastClose });

  const studies = data.studies;
  if (typeof studies === "object" && studies !== null) {
    const record = studies as Record<string, unknown>;
    const keys = [
      ...INDICATOR_DISPLAY_ORDER.filter((key) => key in record),
      ...Object.keys(record).filter(
        (key) => !INDICATOR_DISPLAY_ORDER.includes(key),
      ),
    ];
    for (const key of keys) {
      if (stats.length >= MAX_INDICATOR_STATS) break;
      const value = latestOf(record[key]);
      const formatted = fmt(value, 2);
      if (formatted !== undefined) {
        stats.push({ label: key.replace(/_/g, " "), value: formatted });
      }
    }
  }

  return { subtitle: contextSubtitle(data), stats };
}

function summarizeBacktest(data: Record<string, unknown>): TradingSummary {
  // The tool reports this in-band rather than as an "Error:" string, because
  // the fetch succeeded and only the signals were unusable.
  const inlineError = str(data.error);
  if (inlineError) {
    return { subtitle: contextSubtitle(data), stats: [], error: inlineError };
  }

  const stats: TradingStat[] = [];

  const evaluated = data.signals_evaluated;
  if (typeof evaluated === "number") {
    stats.push({ label: "trades", value: String(evaluated) });
  }

  const winRate = data.win_rate_pct;
  if (typeof winRate === "number") {
    stats.push({
      label: "win rate",
      value: `${fmt(winRate, 1)}%`,
      tone: winRate >= 50 ? "positive" : "negative",
    });
  }

  const expectancy = data.expectancy_r;
  if (typeof expectancy === "number") {
    stats.push({
      label: "expectancy",
      value: `${expectancy >= 0 ? "+" : ""}${fmt(expectancy, 2)}R`,
      tone: expectancy > 0 ? "positive" : expectancy < 0 ? "negative" : "neutral",
    });
  }

  const totalR = data.total_r;
  if (typeof totalR === "number") {
    stats.push({
      label: "total",
      value: `${totalR >= 0 ? "+" : ""}${fmt(totalR, 2)}R`,
      tone: totalR > 0 ? "positive" : totalR < 0 ? "negative" : "neutral",
    });
  }

  const profitFactor = data.profit_factor;
  if (typeof profitFactor === "number") {
    stats.push({
      label: "profit factor",
      value: fmt(profitFactor, 2)!,
      tone: profitFactor >= 1 ? "positive" : "negative",
    });
  }

  const maxDd = data.max_drawdown_r;
  if (typeof maxDd === "number") {
    stats.push({ label: "max DD", value: `${fmt(maxDd, 2)}R` });
  }

  const open = data.still_open;
  if (typeof open === "number" && open > 0) {
    stats.push({ label: "open", value: String(open) });
  }

  return { subtitle: contextSubtitle(data), stats };
}

/**
 * Reduce a trading tool result to a compact stat row.
 *
 * Returns `null` when there is nothing meaningful to show yet (still
 * streaming, unparseable, or not a trading tool) so the caller can fall back
 * to the plain description-labelled step.
 */
export function summarizeTradingResult(
  name: string,
  result: string | undefined,
): TradingSummary | null {
  if (!isTradingTool(name)) {
    return null;
  }

  const payload = parsePayload(result);
  if (!payload) {
    return null;
  }
  if (payload.error) {
    return { stats: [], error: payload.error };
  }

  const data = payload.data!;
  switch (name) {
    case "get_ohlcv":
      return summarizeOhlcv(data);
    case "compute_indicators":
      return summarizeIndicators(data);
    case "backtest_signals":
      return summarizeBacktest(data);
  }
}
