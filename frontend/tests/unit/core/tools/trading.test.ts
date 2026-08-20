import { describe, expect, it } from "vitest";

import { isTradingTool, summarizeTradingResult } from "@/core/tools/trading";

function labels(summary: ReturnType<typeof summarizeTradingResult>) {
  return summary?.stats.map((s) => s.label) ?? [];
}

function valueOf(
  summary: ReturnType<typeof summarizeTradingResult>,
  label: string,
) {
  return summary?.stats.find((s) => s.label === label)?.value;
}

function toneOf(
  summary: ReturnType<typeof summarizeTradingResult>,
  label: string,
) {
  return summary?.stats.find((s) => s.label === label)?.tone;
}

describe("isTradingTool", () => {
  it("recognises the three trading tools", () => {
    expect(isTradingTool("get_ohlcv")).toBe(true);
    expect(isTradingTool("compute_indicators")).toBe(true);
    expect(isTradingTool("backtest_signals")).toBe(true);
  });

  it("rejects everything else", () => {
    expect(isTradingTool("bash")).toBe(false);
    expect(isTradingTool("web_search")).toBe(false);
    expect(isTradingTool("")).toBe(false);
  });
});

describe("summarizeTradingResult — guards", () => {
  it("returns null for non-trading tools", () => {
    expect(summarizeTradingResult("bash", "{}")).toBeNull();
  });

  it("returns null while the result is still undefined", () => {
    expect(summarizeTradingResult("get_ohlcv", undefined)).toBeNull();
  });

  it("returns null for an empty result", () => {
    expect(summarizeTradingResult("get_ohlcv", "")).toBeNull();
  });

  it("returns null for a partial streaming payload rather than half-parsing", () => {
    expect(
      summarizeTradingResult("get_ohlcv", '{"symbol": "GC=F", "bars": [{"o'),
    ).toBeNull();
  });

  it("returns null for a JSON array (tools always return an object)", () => {
    expect(summarizeTradingResult("get_ohlcv", "[1,2,3]")).toBeNull();
  });

  it("surfaces an Error: string as an error, not a crash", () => {
    const summary = summarizeTradingResult(
      "get_ohlcv",
      "Error: no data returned for 'XAUUSD=X'. Spot XAU/USD has no free Yahoo symbol.",
    );
    expect(summary?.error).toContain("XAUUSD=X");
    expect(summary?.stats).toEqual([]);
  });
});

describe("summarizeTradingResult — get_ohlcv", () => {
  const payload = JSON.stringify({
    symbol: "GC=F",
    interval: "15m",
    source: "yfinance",
    count: 100,
    latest: {
      time: "2026-07-31T19:00:00+00:00",
      open: 4107.1,
      high: 4109.4,
      low: 4106.0,
      close: 4108.2,
      volume: 1234,
    },
    bars: [],
  });

  it("builds a symbol/interval/source subtitle", () => {
    expect(summarizeTradingResult("get_ohlcv", payload)?.subtitle).toBe(
      "GC=F · 15m · yfinance",
    );
  });

  it("reports bar count and last close", () => {
    const summary = summarizeTradingResult("get_ohlcv", payload);
    expect(valueOf(summary, "bars")).toBe("100");
    expect(valueOf(summary, "last")).toBe("4,108.2");
  });

  it("renders the timestamp as UTC", () => {
    expect(valueOf(summarizeTradingResult("get_ohlcv", payload), "at")).toBe(
      "2026-07-31 19:00 UTC",
    );
  });

  it("survives a payload with no latest bar", () => {
    const summary = summarizeTradingResult(
      "get_ohlcv",
      JSON.stringify({ symbol: "AAPL", count: 0, latest: null }),
    );
    expect(summary?.error).toBeUndefined();
    expect(valueOf(summary, "bars")).toBe("0");
  });
});

describe("summarizeTradingResult — compute_indicators", () => {
  const payload = JSON.stringify({
    symbol: "GC=F",
    interval: "15m",
    source: "yfinance",
    bars_used: 300,
    last_close: 4108.2,
    studies: {
      ema_9: [4105.1, 4105.85],
      ema_21: [4104.0, 4104.41],
      rsi_14: [53.2, 54.0],
      atr_14: [6.4, 6.46],
      vwap: [4109.0, 4109.02],
    },
  });

  it("shows the last close first", () => {
    expect(
      labels(summarizeTradingResult("compute_indicators", payload))[0],
    ).toBe("close");
  });

  it("takes the latest value of each series, not the first", () => {
    const summary = summarizeTradingResult("compute_indicators", payload);
    expect(valueOf(summary, "rsi 14")).toBe("54");
    expect(valueOf(summary, "ema 9")).toBe("4,105.85");
  });

  it("orders indicators deterministically so the row does not reshuffle", () => {
    const forward = labels(
      summarizeTradingResult("compute_indicators", payload),
    );
    const reordered = JSON.stringify({
      ...JSON.parse(payload),
      studies: {
        vwap: [4109.02],
        atr_14: [6.46],
        rsi_14: [54.0],
        ema_21: [4104.41],
        ema_9: [4105.85],
      },
    });
    expect(
      labels(summarizeTradingResult("compute_indicators", reordered)),
    ).toEqual(forward);
  });

  it("caps how many indicators are shown", () => {
    const many: Record<string, number[]> = {};
    for (let i = 0; i < 30; i++) many[`custom_${i}`] = [i];
    const summary = summarizeTradingResult(
      "compute_indicators",
      JSON.stringify({ symbol: "X", last_close: 1, studies: many }),
    );
    expect(summary!.stats.length).toBeLessThanOrEqual(6);
  });

  it("skips series that are entirely null during warm-up", () => {
    const summary = summarizeTradingResult(
      "compute_indicators",
      JSON.stringify({
        symbol: "X",
        last_close: 10,
        studies: { rsi_14: [null, null], atr_14: [1.5] },
      }),
    );
    expect(labels(summary)).not.toContain("rsi 14");
    expect(valueOf(summary, "atr 14")).toBe("1.5");
  });

  it("picks the last non-null value when a series ends with nulls", () => {
    const summary = summarizeTradingResult(
      "compute_indicators",
      JSON.stringify({
        symbol: "X",
        last_close: 10,
        studies: { rsi_14: [40, 55, null] },
      }),
    );
    expect(valueOf(summary, "rsi 14")).toBe("55");
  });
});

describe("summarizeTradingResult — backtest_signals", () => {
  const winning = JSON.stringify({
    symbol: "BTC/USDT",
    interval: "1h",
    source: "ccxt:binance",
    signals_evaluated: 5,
    closed_trades: 5,
    still_open: 0,
    wins: 3,
    losses: 2,
    win_rate_pct: 60.0,
    expectancy_r: 0.44,
    total_r: 2.2,
    profit_factor: 3.0,
    max_drawdown_r: 1.0,
    trades: [],
  });

  it("reports the headline metrics", () => {
    const summary = summarizeTradingResult("backtest_signals", winning);
    expect(valueOf(summary, "trades")).toBe("5");
    expect(valueOf(summary, "win rate")).toBe("60%");
    expect(valueOf(summary, "expectancy")).toBe("+0.44R");
    expect(valueOf(summary, "profit factor")).toBe("3");
    expect(valueOf(summary, "max DD")).toBe("1R");
  });

  it("tones a profitable result positive", () => {
    const summary = summarizeTradingResult("backtest_signals", winning);
    expect(toneOf(summary, "expectancy")).toBe("positive");
    expect(toneOf(summary, "total")).toBe("positive");
    expect(toneOf(summary, "win rate")).toBe("positive");
    expect(toneOf(summary, "profit factor")).toBe("positive");
  });

  it("tones a losing result negative and keeps the sign", () => {
    const losing = JSON.stringify({
      symbol: "GC=F",
      signals_evaluated: 4,
      win_rate_pct: 25.0,
      expectancy_r: -0.6,
      total_r: -2.4,
      profit_factor: 0.4,
    });
    const summary = summarizeTradingResult("backtest_signals", losing);
    expect(valueOf(summary, "expectancy")).toBe("-0.6R");
    expect(valueOf(summary, "total")).toBe("-2.4R");
    expect(toneOf(summary, "expectancy")).toBe("negative");
    expect(toneOf(summary, "win rate")).toBe("negative");
    expect(toneOf(summary, "profit factor")).toBe("negative");
  });

  it("shows open trades only when there are any", () => {
    expect(
      labels(summarizeTradingResult("backtest_signals", winning)),
    ).not.toContain("open");
    const withOpen = JSON.stringify({
      symbol: "X",
      signals_evaluated: 3,
      still_open: 2,
    });
    expect(
      valueOf(summarizeTradingResult("backtest_signals", withOpen), "open"),
    ).toBe("2");
  });

  it("handles a null win rate when nothing has closed yet", () => {
    const summary = summarizeTradingResult(
      "backtest_signals",
      JSON.stringify({
        symbol: "X",
        signals_evaluated: 1,
        still_open: 1,
        win_rate_pct: null,
        expectancy_r: 0,
        total_r: 0,
        profit_factor: null,
      }),
    );
    expect(labels(summary)).not.toContain("win rate");
    expect(labels(summary)).not.toContain("profit factor");
    expect(toneOf(summary, "expectancy")).toBe("neutral");
  });

  it("surfaces the in-band error when no signals could be evaluated", () => {
    const summary = summarizeTradingResult(
      "backtest_signals",
      JSON.stringify({
        symbol: "GC=F",
        error: "no signals could be evaluated",
        skipped: [{ reason: "zero risk" }],
      }),
    );
    expect(summary?.error).toBe("no signals could be evaluated");
    expect(summary?.stats).toEqual([]);
  });
});
