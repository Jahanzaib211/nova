"""Free, keyless OHLCV providers: yfinance and ccxt public endpoints.

Both are optional dependencies (``uv sync --extra trading``). They are
imported lazily so a default Nova install is unaffected and so a missing
package produces an actionable hint rather than an ImportError traceback
at module load — the same convention ``deerflow.reflection`` uses for
optional model providers.

No API keys anywhere. yfinance covers equities, FX, and futures (including
``GC=F`` and ``XAUUSD=X`` for gold); ccxt covers crypto spot via each
exchange's public market-data endpoints, which do not require credentials.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

# yfinance rejects intervals it doesn't know, so map the common aliases a
# model is likely to produce onto its vocabulary.
_YF_INTERVAL_ALIASES = {
    "1min": "1m",
    "5min": "5m",
    "15min": "15m",
    "30min": "30m",
    "60min": "1h",
    "1hour": "1h",
    "60m": "1h",
    "4h": "1h",  # yfinance has no 4h; caller must resample. Flagged in fetch_ohlcv.
    "daily": "1d",
    "1day": "1d",
    "weekly": "1wk",
    "monthly": "1mo",
}

_YF_VALID = {"1m", "2m", "5m", "15m", "30m", "60m", "90m", "1h", "1d", "5d", "1wk", "1mo", "3mo"}

# Yahoo caps intraday history. Requesting more silently returns an empty
# frame, which looks like "no data for this symbol" — so we clamp and say so.
_YF_MAX_DAYS = {"1m": 7, "2m": 59, "5m": 59, "15m": 59, "30m": 59, "60m": 729, "90m": 59, "1h": 729}

_INTERVAL_MINUTES = {
    "1m": 1,
    "2m": 2,
    "5m": 5,
    "15m": 15,
    "30m": 30,
    "60m": 60,
    "1h": 60,
    "90m": 90,
    "1d": 1440,
    "5d": 7200,
    "1wk": 10080,
    "1mo": 43200,
}


class MarketDataError(RuntimeError):
    """Raised for anything a caller can act on: bad symbol, missing dep, no data."""


def symbol_hint(symbol: str) -> str:
    """Targeted advice for symbols that are commonly written the wrong way.

    Verified against the live Yahoo and Binance endpoints on 2026-08-01 —
    notably ``XAUUSD=X`` and ``XAU=X`` return an *empty frame* rather than an
    error, which is indistinguishable from "market closed" unless we say so.
    """
    upper = symbol.upper()

    if "XAU" in upper or upper in {"GOLD", "SPOT GOLD"}:
        return (
            " Spot XAU/USD has no free Yahoo symbol ('XAUUSD', 'XAUUSD=X' and 'XAU=X' all return nothing)."
            " Use 'GC=F' (COMEX front-month gold futures, ~1% above spot from carry) or 'MGC=F' (micro gold);"
            " for a spot-tracking alternative use source='ccxt' with 'PAXG/USDT' or 'XAUT/USDT' (tokenized gold)."
        )

    # Six-letter FX pair written without Yahoo's suffix.
    if len(upper) == 6 and upper.isalpha():
        return f" Yahoo FX pairs need a '=X' suffix — try '{upper}=X'."

    if "/" in symbol:
        return " Symbols with '/' are ccxt pairs; pass source='ccxt' (and an exchange) rather than fetching them from Yahoo."

    return ""


def _require(module: str, extra_hint: str):
    try:
        return __import__(module)
    except ImportError as exc:
        raise MarketDataError(f"{module} is not installed. Install the optional trading extra with: uv sync --extra trading   (or: uv add {extra_hint})") from exc


def normalize_interval(interval: str) -> str:
    key = interval.strip().lower()
    return _YF_INTERVAL_ALIASES.get(key, key)


def looks_like_crypto_pair(symbol: str) -> bool:
    """``BTC/USDT`` style pairs go to ccxt; everything else to Yahoo."""
    return "/" in symbol


def _yf_period_for(interval: str, limit: int) -> str:
    """Smallest Yahoo `period` that can contain `limit` bars of `interval`."""
    minutes = _INTERVAL_MINUTES.get(interval, 1440)
    # 1.6x headroom: sessions have gaps (weekends, holidays, market hours),
    # so N bars of history spans materially more than N intervals of wall time.
    days_needed = max(1, int((limit * minutes * 1.6) / 1440) + 1)
    cap = _YF_MAX_DAYS.get(interval)
    if cap:
        days_needed = min(days_needed, cap)
    if days_needed <= 7:
        return f"{days_needed}d"
    if days_needed <= 60:
        return f"{days_needed}d"
    if days_needed <= 729:
        return f"{days_needed}d"
    years = max(1, days_needed // 365)
    return f"{min(years, 10)}y"


def fetch_yfinance(symbol: str, interval: str, limit: int) -> list[dict[str, Any]]:
    yf = _require("yfinance", "yfinance")
    interval = normalize_interval(interval)
    if interval not in _YF_VALID:
        raise MarketDataError(f"interval {interval!r} is not supported by Yahoo Finance. Valid: {sorted(_YF_VALID)}. (For 4h, fetch 1h and resample.)")

    period = _yf_period_for(interval, limit)
    ticker = yf.Ticker(symbol)
    frame = ticker.history(period=period, interval=interval, auto_adjust=False)

    if frame is None or frame.empty:
        cap = _YF_MAX_DAYS.get(interval)
        cap_note = f" Yahoo only serves ~{cap} days of {interval} history." if cap else ""
        raise MarketDataError(f"no data returned for {symbol!r} at {interval} over {period}.{symbol_hint(symbol)}{cap_note}")

    bars: list[dict[str, Any]] = []
    for ts, row in frame.iterrows():
        when = ts.to_pydatetime()
        if when.tzinfo is None:
            when = when.replace(tzinfo=UTC)
        bars.append(
            {
                "time": when.astimezone(UTC).isoformat(),
                "open": float(row["Open"]),
                "high": float(row["High"]),
                "low": float(row["Low"]),
                "close": float(row["Close"]),
                "volume": float(row.get("Volume", 0) or 0),
            }
        )
    return bars[-limit:]


def fetch_ccxt(symbol: str, interval: str, limit: int, exchange_id: str = "binance") -> list[dict[str, Any]]:
    ccxt = _require("ccxt", "ccxt")
    interval = normalize_interval(interval)

    try:
        exchange_cls = getattr(ccxt, exchange_id)
    except AttributeError as exc:
        raise MarketDataError(f"unknown ccxt exchange {exchange_id!r}") from exc

    # No credentials: only public market-data endpoints are used.
    exchange = exchange_cls({"enableRateLimit": True})
    timeframes = getattr(exchange, "timeframes", None) or {}
    if timeframes and interval not in timeframes:
        raise MarketDataError(f"{exchange_id} does not offer timeframe {interval!r}. Available: {sorted(timeframes)[:20]}")

    try:
        raw = exchange.fetch_ohlcv(symbol, timeframe=interval, limit=limit)
    except Exception as exc:
        raise MarketDataError(f"{exchange_id} rejected {symbol!r} at {interval}: {exc}") from exc
    finally:
        close = getattr(exchange, "close", None)
        if callable(close):
            try:
                close()
            except Exception:
                pass

    if not raw:
        raise MarketDataError(f"no data returned for {symbol!r} on {exchange_id} at {interval}")

    return [
        {
            "time": datetime.fromtimestamp(row[0] / 1000, tz=UTC).isoformat(),
            "open": float(row[1]),
            "high": float(row[2]),
            "low": float(row[3]),
            "close": float(row[4]),
            "volume": float(row[5] or 0),
        }
        for row in raw
    ]


def fetch_ohlcv(symbol: str, interval: str = "15m", limit: int = 200, source: str = "auto", exchange: str = "binance") -> tuple[list[dict[str, Any]], str]:
    """Fetch normalized OHLCV bars. Returns ``(bars, resolved_source)``.

    ``source="auto"`` routes ``BASE/QUOTE`` pairs to ccxt and everything else
    to Yahoo, which is the split that matches how symbols are actually written.
    """
    resolved = source
    if source == "auto":
        resolved = "ccxt" if looks_like_crypto_pair(symbol) else "yfinance"

    if resolved == "yfinance":
        return fetch_yfinance(symbol, interval, limit), "yfinance"
    if resolved == "ccxt":
        return fetch_ccxt(symbol, interval, limit, exchange), f"ccxt:{exchange}"
    raise MarketDataError(f"unknown source {source!r} (expected 'auto', 'yfinance', or 'ccxt')")
