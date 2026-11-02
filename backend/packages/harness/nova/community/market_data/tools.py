"""Agent-facing trading tools: get_ohlcv, compute_indicators, backtest_signals.

Follows the community-tool convention (see ``community/ddg_search/tools.py``):
``@tool(name, parse_docstring=True)``, ``description`` first so the frontend
has a label, config overrides read from ``get_app_config()``, and a JSON
string as the result.

These run in the gateway/harness process, not the sandbox — they hand market
data straight to the model. Code the agent writes inside the sandbox still
manages its own dependencies; that path is unchanged.
"""

from __future__ import annotations

import json
from typing import Any

from langchain.tools import tool

from deerflow.community.market_data import indicators as ind
from deerflow.community.market_data.providers import MarketDataError, fetch_ohlcv

_MAX_LIMIT = 1000
_DEFAULT_TAIL = 5


def _tool_extra(name: str) -> dict[str, Any]:
    """Config overrides for a tool, or {} when unconfigured."""
    try:
        from deerflow.config import get_app_config

        config = get_app_config().get_tool_config(name)
    except Exception:
        return {}
    if config is None:
        return {}
    return dict(getattr(config, "model_extra", None) or {})


def _session_keys(bars: list[dict[str, Any]]) -> list[str]:
    """UTC date per bar — the anchor session VWAP resets on."""
    return [str(b.get("time", ""))[:10] for b in bars]


def _tail(series: list[Any], count: int) -> list[Any]:
    return [round(v, 6) if isinstance(v, float) else v for v in series[-count:]]


@tool("get_ohlcv", parse_docstring=True)
def get_ohlcv_tool(
    description: str,
    symbol: str,
    interval: str = "15m",
    limit: int = 200,
    source: str = "auto",
    exchange: str = "binance",
) -> str:
    """Fetch OHLCV candles for any symbol from free, keyless market data.

    Yahoo Finance covers equities, FX, indices, and futures; ccxt covers
    crypto spot.

    GOLD: there is no free spot XAU/USD feed — "XAUUSD", "XAUUSD=X" and
    "XAU=X" all return nothing on Yahoo. Use ``GC=F`` (COMEX front-month
    futures, roughly 1% above spot because of carry) or ``MGC=F`` (micro
    gold). For a spot-tracking series instead, use source="ccxt" with
    ``PAXG/USDT`` or ``XAUT/USDT`` (tokenized gold).

    FX pairs need Yahoo's suffix: ``EURUSD=X``, not ``EURUSD``.

    Returns JSON: ``{symbol, interval, source, count, bars: [{time, open,
    high, low, close, volume}], latest}``.

    Args:
        description: Why you are fetching this data. ALWAYS PROVIDE THIS FIRST.
        symbol: Ticker. Yahoo style ("GC=F", "EURUSD=X", "AAPL") or ccxt pair ("BTC/USDT").
        interval: Candle size — 1m, 5m, 15m, 30m, 1h, 1d, 1wk. Default 15m.
        limit: Number of most-recent candles to return (max 1000). Default 200.
        source: "auto" (pairs with "/" go to ccxt, else Yahoo), "yfinance", or "ccxt".
        exchange: ccxt exchange id when source resolves to ccxt. Default "binance".
    """
    extra = _tool_extra("get_ohlcv")
    limit = max(1, min(int(limit), int(extra.get("max_limit", _MAX_LIMIT))))
    source = extra.get("source", source)
    exchange = extra.get("exchange", exchange)

    try:
        bars, resolved = fetch_ohlcv(symbol, interval, limit, source, exchange)
    except MarketDataError as exc:
        return f"Error: {exc}"
    except Exception as exc:
        return f"Error: failed to fetch {symbol!r} at {interval}: {exc}"

    return json.dumps(
        {
            "symbol": symbol,
            "interval": interval,
            "source": resolved,
            "count": len(bars),
            "latest": bars[-1] if bars else None,
            "bars": bars,
        },
        ensure_ascii=False,
    )


@tool("compute_indicators", parse_docstring=True)
def compute_indicators_tool(
    description: str,
    symbol: str,
    interval: str = "15m",
    limit: int = 200,
    studies: str = "ema,rsi,atr,vwap",
    tail: int = _DEFAULT_TAIL,
    source: str = "auto",
    exchange: str = "binance",
) -> str:
    """Compute technical indicators over freshly fetched candles.

    Available studies: ``sma``, ``ema``, ``rsi``, ``macd``, ``atr``, ``adx``,
    ``bollinger``, ``vwap`` (session-anchored VWAP with ±1.5σ / ±2.5σ bands).

    Only the most recent ``tail`` values of each series are returned — the
    full series would flood the context and the last few bars are what a
    decision actually turns on.

    Args:
        description: Why you are computing these. ALWAYS PROVIDE THIS FIRST.
        symbol: Ticker, same format as get_ohlcv.
        interval: Candle size. Default 15m.
        limit: Candles to fetch for the computation (more = longer warm-up covered). Default 200.
        studies: Comma-separated study names. Default "ema,rsi,atr,vwap".
        tail: How many recent values of each series to return. Default 5.
        source: "auto", "yfinance", or "ccxt".
        exchange: ccxt exchange id when source resolves to ccxt.
    """
    limit = max(1, min(int(limit), _MAX_LIMIT))
    tail = max(1, min(int(tail), 100))

    try:
        bars, resolved = fetch_ohlcv(symbol, interval, limit, source, exchange)
    except MarketDataError as exc:
        return f"Error: {exc}"
    except Exception as exc:
        return f"Error: failed to fetch {symbol!r} at {interval}: {exc}"

    closes = [b["close"] for b in bars]
    highs = [b["high"] for b in bars]
    lows = [b["low"] for b in bars]
    volumes = [b["volume"] for b in bars]

    wanted = [s.strip().lower() for s in studies.split(",") if s.strip()]
    out: dict[str, Any] = {}
    unknown: list[str] = []

    for study in wanted:
        if study == "sma":
            out["sma_20"] = _tail(ind.sma(closes, 20), tail)
            out["sma_50"] = _tail(ind.sma(closes, 50), tail)
        elif study == "ema":
            out["ema_9"] = _tail(ind.ema(closes, 9), tail)
            out["ema_21"] = _tail(ind.ema(closes, 21), tail)
            out["ema_50"] = _tail(ind.ema(closes, 50), tail)
        elif study == "rsi":
            out["rsi_14"] = _tail(ind.rsi(closes, 14), tail)
        elif study == "macd":
            out.update({f"macd_{k}": _tail(v, tail) for k, v in ind.macd(closes).items()})
        elif study == "atr":
            out["atr_14"] = _tail(ind.atr(highs, lows, closes, 14), tail)
        elif study == "adx":
            out.update({f"adx_{k}": _tail(v, tail) for k, v in ind.adx(highs, lows, closes, 14).items()})
        elif study == "bollinger":
            out.update({f"bb_{k}": _tail(v, tail) for k, v in ind.bollinger(closes, 20, 2.0).items()})
        elif study == "vwap":
            vwap = ind.session_vwap(highs, lows, closes, volumes, _session_keys(bars))
            out.update({f"vwap_{k}" if k != "vwap" else "vwap": _tail(v, tail) for k, v in vwap.items()})
        else:
            unknown.append(study)

    payload: dict[str, Any] = {
        "symbol": symbol,
        "interval": interval,
        "source": resolved,
        "bars_used": len(bars),
        "last_close": closes[-1] if closes else None,
        "last_time": bars[-1]["time"] if bars else None,
        "tail": tail,
        "studies": out,
    }
    if unknown:
        payload["unknown_studies"] = unknown
        payload["available_studies"] = ["sma", "ema", "rsi", "macd", "atr", "adx", "bollinger", "vwap"]
    return json.dumps(payload, ensure_ascii=False)


def _evaluate_signal(bars: list[dict[str, Any]], start: int, side: str, entry: float, stop: float, target: float) -> dict[str, Any]:
    """Walk forward from `start` until stop or target is touched.

    When a single bar's range spans both levels we resolve it as a loss.
    Intrabar sequence is unknowable from OHLC alone, and assuming the
    favourable order is how backtests quietly manufacture edge that does
    not survive live trading.
    """
    long = side.lower() in {"long", "buy"}
    for i in range(start, len(bars)):
        high = bars[i]["high"]
        low = bars[i]["low"]
        hit_stop = low <= stop if long else high >= stop
        hit_target = high >= target if long else low <= target
        if hit_stop and hit_target:
            return {"outcome": "loss", "bars_held": i - start, "exit": stop, "ambiguous_bar": True}
        if hit_stop:
            return {"outcome": "loss", "bars_held": i - start, "exit": stop, "ambiguous_bar": False}
        if hit_target:
            return {"outcome": "win", "bars_held": i - start, "exit": target, "ambiguous_bar": False}
    return {"outcome": "open", "bars_held": len(bars) - start, "exit": bars[-1]["close"] if bars else entry, "ambiguous_bar": False}


@tool("backtest_signals", parse_docstring=True)
def backtest_signals_tool(
    description: str,
    symbol: str,
    signals: str,
    interval: str = "15m",
    limit: int = 500,
    source: str = "auto",
    exchange: str = "binance",
) -> str:
    """Replay a list of entry signals against historical candles.

    For each signal, walks forward from its timestamp until price touches the
    stop or the target, then aggregates win rate, expectancy (in R), profit
    factor, and max consecutive losses.

    A bar that touches both stop and target is scored as a **loss** — OHLC
    data cannot reveal intrabar order, and assuming otherwise inflates
    results. The count of such bars is reported so you can judge the impact.

    Args:
        description: Why you are backtesting. ALWAYS PROVIDE THIS FIRST.
        symbol: Ticker, same format as get_ohlcv.
        signals: JSON array of signal objects. Each needs the keys time (ISO8601), side ("long" or "short"), entry, stop, and target. Example: [{"time": "2026-07-30T09:15:00+00:00", "side": "long", "entry": 4050.5, "stop": 4044.0, "target": 4070.0}]
        interval: Candle size the signals were generated on. Default 15m.
        limit: Candles of history to replay against. Default 500.
        source: "auto", "yfinance", or "ccxt".
        exchange: ccxt exchange id when source resolves to ccxt.
    """
    try:
        parsed = json.loads(signals)
    except json.JSONDecodeError as exc:
        return f"Error: `signals` is not valid JSON ({exc}). Expected an array of {{time, side, entry, stop, target}} objects."
    if not isinstance(parsed, list) or not parsed:
        return "Error: `signals` must be a non-empty JSON array of {time, side, entry, stop, target} objects."

    limit = max(1, min(int(limit), _MAX_LIMIT))
    try:
        bars, resolved = fetch_ohlcv(symbol, interval, limit, source, exchange)
    except MarketDataError as exc:
        return f"Error: {exc}"
    except Exception as exc:
        return f"Error: failed to fetch {symbol!r} at {interval}: {exc}"

    times = [b["time"] for b in bars]
    results: list[dict[str, Any]] = []
    skipped: list[dict[str, Any]] = []

    for signal in parsed:
        if not isinstance(signal, dict):
            skipped.append({"signal": signal, "reason": "not an object"})
            continue
        try:
            when = str(signal["time"])
            side = str(signal["side"])
            entry = float(signal["entry"])
            stop = float(signal["stop"])
            target = float(signal["target"])
        except (KeyError, TypeError, ValueError) as exc:
            skipped.append({"signal": signal, "reason": f"missing/invalid field: {exc}"})
            continue

        start = next((i for i, t in enumerate(times) if t >= when), None)
        if start is None:
            skipped.append({"signal": signal, "reason": "timestamp is after the last available candle"})
            continue

        risk = abs(entry - stop)
        if risk == 0:
            skipped.append({"signal": signal, "reason": "entry equals stop (zero risk)"})
            continue

        outcome = _evaluate_signal(bars, start, side, entry, stop, target)
        reward = abs(target - entry)
        r_multiple = 0.0
        if outcome["outcome"] == "win":
            r_multiple = reward / risk
        elif outcome["outcome"] == "loss":
            r_multiple = -1.0
        else:
            direction = 1 if side.lower() in {"long", "buy"} else -1
            r_multiple = direction * (outcome["exit"] - entry) / risk

        results.append({**outcome, "time": when, "side": side, "r_multiple": round(r_multiple, 4), "planned_rr": round(reward / risk, 3)})

    if not results:
        return json.dumps({"symbol": symbol, "interval": interval, "source": resolved, "error": "no signals could be evaluated", "skipped": skipped}, ensure_ascii=False)

    wins = [r for r in results if r["outcome"] == "win"]
    losses = [r for r in results if r["outcome"] == "loss"]
    closed = wins + losses
    gross_profit = sum(r["r_multiple"] for r in wins)
    gross_loss = abs(sum(r["r_multiple"] for r in losses))

    max_consec_losses = 0
    streak = 0
    for r in results:
        if r["outcome"] == "loss":
            streak += 1
            max_consec_losses = max(max_consec_losses, streak)
        elif r["outcome"] == "win":
            streak = 0

    equity = 0.0
    peak = 0.0
    max_dd = 0.0
    for r in results:
        equity += r["r_multiple"]
        peak = max(peak, equity)
        max_dd = max(max_dd, peak - equity)

    return json.dumps(
        {
            "symbol": symbol,
            "interval": interval,
            "source": resolved,
            "bars_replayed": len(bars),
            "signals_evaluated": len(results),
            "closed_trades": len(closed),
            "still_open": len(results) - len(closed),
            "wins": len(wins),
            "losses": len(losses),
            "win_rate_pct": round(100.0 * len(wins) / len(closed), 2) if closed else None,
            "expectancy_r": round(sum(r["r_multiple"] for r in results) / len(results), 4),
            "total_r": round(equity, 4),
            "profit_factor": round(gross_profit / gross_loss, 3) if gross_loss else None,
            "max_drawdown_r": round(max_dd, 4),
            "max_consecutive_losses": max_consec_losses,
            "ambiguous_bars": sum(1 for r in results if r.get("ambiguous_bar")),
            "avg_bars_held": round(sum(r["bars_held"] for r in results) / len(results), 2),
            "trades": results,
            "skipped": skipped,
        },
        ensure_ascii=False,
    )
