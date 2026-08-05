"""Technical indicators in pure Python — no numpy, no pandas, no pandas-ta.

Deliberately dependency-free. Two reasons:

1. ``pandas-ta`` is effectively unmaintained and breaks under numpy 2, which
   is what a fresh install of this project resolves to.
2. Keeping these pure means ``compute_indicators`` and ``backtest_signals``
   work with *zero* extra dependencies — only ``get_ohlcv`` needs the
   optional ``trading`` extra. An agent that already has bars in hand (from
   a CSV, an upload, or its own scraper) can analyse them on a default Nova
   install.

Every function takes plain lists and returns a list of the *same length* as
its input, left-padded with ``None`` through the warm-up period. Aligned
output matters: the caller zips these back onto bars by index, and a series
that silently starts short shifts every signal by the warm-up length.
"""

from __future__ import annotations

from collections.abc import Sequence

Number = float | int
Series = list[float | None]


def sma(values: Sequence[Number], period: int) -> Series:
    """Simple moving average."""
    if period <= 0:
        raise ValueError("period must be positive")
    out: Series = [None] * len(values)
    if len(values) < period:
        return out
    window = float(sum(values[:period]))
    out[period - 1] = window / period
    for i in range(period, len(values)):
        window += float(values[i]) - float(values[i - period])
        out[i] = window / period
    return out


def ema(values: Sequence[Number], period: int) -> Series:
    """Exponential moving average, seeded with the SMA of the first window.

    Seeding with an SMA rather than the first value is what TradingView's
    ``ta.ema`` does; seeding with ``values[0]`` produces a visibly different
    curve for the first few hundred bars and would not reconcile against a
    chart.
    """
    if period <= 0:
        raise ValueError("period must be positive")
    out: Series = [None] * len(values)
    if len(values) < period:
        return out
    alpha = 2.0 / (period + 1.0)
    prev = float(sum(values[:period])) / period
    out[period - 1] = prev
    for i in range(period, len(values)):
        prev = (float(values[i]) - prev) * alpha + prev
        out[i] = prev
    return out


def rsi(values: Sequence[Number], period: int = 14) -> Series:
    """Relative Strength Index using Wilder's smoothing (matches ``ta.rsi``)."""
    out: Series = [None] * len(values)
    if len(values) <= period:
        return out

    gains = 0.0
    losses = 0.0
    for i in range(1, period + 1):
        change = float(values[i]) - float(values[i - 1])
        gains += max(change, 0.0)
        losses += max(-change, 0.0)
    avg_gain = gains / period
    avg_loss = losses / period
    out[period] = 100.0 if avg_loss == 0 else 100.0 - (100.0 / (1.0 + avg_gain / avg_loss))

    for i in range(period + 1, len(values)):
        change = float(values[i]) - float(values[i - 1])
        avg_gain = (avg_gain * (period - 1) + max(change, 0.0)) / period
        avg_loss = (avg_loss * (period - 1) + max(-change, 0.0)) / period
        out[i] = 100.0 if avg_loss == 0 else 100.0 - (100.0 / (1.0 + avg_gain / avg_loss))
    return out


def macd(values: Sequence[Number], fast: int = 12, slow: int = 26, signal: int = 9) -> dict[str, Series]:
    """MACD line, signal line, and histogram."""
    fast_ema = ema(values, fast)
    slow_ema = ema(values, slow)
    line: Series = [None if (f is None or s is None) else f - s for f, s in zip(fast_ema, slow_ema, strict=True)]

    # The signal EMA runs over the MACD line's defined region only, then is
    # padded back to full length so all three series stay index-aligned.
    defined = [v for v in line if v is not None]
    offset = len(line) - len(defined)
    signal_core = ema(defined, signal) if defined else []
    signal_series: Series = [None] * offset + list(signal_core)
    signal_series += [None] * (len(line) - len(signal_series))

    hist: Series = [None if (m is None or s is None) else m - s for m, s in zip(line, signal_series, strict=True)]
    return {"macd": line, "signal": signal_series, "histogram": hist}


def true_range(high: Sequence[Number], low: Sequence[Number], close: Sequence[Number]) -> Series:
    out: Series = [None] * len(high)
    if not high:
        return out
    out[0] = float(high[0]) - float(low[0])
    for i in range(1, len(high)):
        prev_close = float(close[i - 1])
        out[i] = max(
            float(high[i]) - float(low[i]),
            abs(float(high[i]) - prev_close),
            abs(float(low[i]) - prev_close),
        )
    return out


def atr(high: Sequence[Number], low: Sequence[Number], close: Sequence[Number], period: int = 14) -> Series:
    """Average True Range with Wilder smoothing (matches ``ta.atr``)."""
    tr = true_range(high, low, close)
    out: Series = [None] * len(tr)
    values = [v for v in tr if v is not None]
    if len(values) < period:
        return out
    prev = sum(values[:period]) / period
    out[period - 1] = prev
    for i in range(period, len(tr)):
        current = tr[i]
        if current is None:
            continue
        prev = (prev * (period - 1) + current) / period
        out[i] = prev
    return out


def adx(high: Sequence[Number], low: Sequence[Number], close: Sequence[Number], period: int = 14) -> dict[str, Series]:
    """Wilder's ADX with +DI / -DI (matches ``ta.dmi``)."""
    n = len(high)
    empty: Series = [None] * n
    if n <= period * 2:
        return {"adx": empty, "plus_di": list(empty), "minus_di": list(empty)}

    plus_dm: list[float] = [0.0] * n
    minus_dm: list[float] = [0.0] * n
    for i in range(1, n):
        up = float(high[i]) - float(high[i - 1])
        down = float(low[i - 1]) - float(low[i])
        plus_dm[i] = up if (up > down and up > 0) else 0.0
        minus_dm[i] = down if (down > up and down > 0) else 0.0

    tr = [v or 0.0 for v in true_range(high, low, close)]

    # Wilder's running sums, seeded over the first `period` bars.
    tr_sum = sum(tr[1 : period + 1])
    plus_sum = sum(plus_dm[1 : period + 1])
    minus_sum = sum(minus_dm[1 : period + 1])

    plus_di: Series = [None] * n
    minus_di: Series = [None] * n
    dx: list[float | None] = [None] * n

    def _di(dm_sum: float, trs: float) -> float:
        return 0.0 if trs == 0 else 100.0 * dm_sum / trs

    idx = period
    plus_di[idx] = _di(plus_sum, tr_sum)
    minus_di[idx] = _di(minus_sum, tr_sum)
    dx[idx] = _dx(plus_di[idx], minus_di[idx])

    for i in range(period + 1, n):
        tr_sum = tr_sum - (tr_sum / period) + tr[i]
        plus_sum = plus_sum - (plus_sum / period) + plus_dm[i]
        minus_sum = minus_sum - (minus_sum / period) + minus_dm[i]
        plus_di[i] = _di(plus_sum, tr_sum)
        minus_di[i] = _di(minus_sum, tr_sum)
        dx[i] = _dx(plus_di[i], minus_di[i])

    adx_series: Series = [None] * n
    dx_defined = [(i, v) for i, v in enumerate(dx) if v is not None]
    if len(dx_defined) >= period:
        start_idx = dx_defined[period - 1][0]
        prev = sum(v for _, v in dx_defined[:period]) / period
        adx_series[start_idx] = prev
        for i, v in dx_defined[period:]:
            prev = (prev * (period - 1) + v) / period
            adx_series[i] = prev

    return {"adx": adx_series, "plus_di": plus_di, "minus_di": minus_di}


def _dx(plus: float | None, minus: float | None) -> float:
    if plus is None or minus is None:
        return 0.0
    total = plus + minus
    return 0.0 if total == 0 else 100.0 * abs(plus - minus) / total


def bollinger(values: Sequence[Number], period: int = 20, stddev: float = 2.0) -> dict[str, Series]:
    """Bollinger Bands around an SMA basis (population stdev, as TradingView uses)."""
    basis = sma(values, period)
    upper: Series = [None] * len(values)
    lower: Series = [None] * len(values)
    for i in range(period - 1, len(values)):
        mean = basis[i]
        if mean is None:
            continue
        window = [float(v) for v in values[i - period + 1 : i + 1]]
        variance = sum((v - mean) ** 2 for v in window) / period
        dev = stddev * (variance**0.5)
        upper[i] = mean + dev
        lower[i] = mean - dev
    return {"basis": basis, "upper": upper, "lower": lower}


def session_vwap(
    high: Sequence[Number],
    low: Sequence[Number],
    close: Sequence[Number],
    volume: Sequence[Number],
    session_keys: Sequence[str],
    bands: Sequence[float] = (1.5, 2.5),
) -> dict[str, Series]:
    """Session-anchored VWAP with volume-weighted standard-deviation bands.

    ``session_keys`` is one key per bar (typically the UTC date); VWAP resets
    whenever the key changes. That reset is the whole point — a VWAP that
    runs continuously across days is a different, far less useful indicator
    than the session VWAP a mean-reversion strategy anchors to.

    Bands are ±k volume-weighted standard deviations of typical price, which
    is what TradingView's "VWAP Bands" draws.

    Returns ``{"vwap": [...], "upper_1.5": [...], "lower_1.5": [...], ...}``.
    """
    n = len(close)
    vwap: Series = [None] * n
    out: dict[str, Series] = {"vwap": vwap}
    for band in bands:
        out[f"upper_{band}"] = [None] * n
        out[f"lower_{band}"] = [None] * n

    cum_pv = 0.0
    cum_v = 0.0
    cum_pv2 = 0.0
    current_key: str | None = None

    for i in range(n):
        key = session_keys[i] if i < len(session_keys) else None
        if key != current_key:
            current_key = key
            cum_pv = cum_v = cum_pv2 = 0.0

        typical = (float(high[i]) + float(low[i]) + float(close[i])) / 3.0
        vol = float(volume[i]) if i < len(volume) and volume[i] else 0.0
        # Zero-volume bars are common in FX/CFD feeds; fall back to equal
        # weighting so VWAP stays defined instead of dividing by zero.
        weight = vol if vol > 0 else 1.0

        cum_pv += typical * weight
        cum_pv2 += typical * typical * weight
        cum_v += weight

        if cum_v <= 0:
            continue
        mean = cum_pv / cum_v
        vwap[i] = mean
        variance = max(cum_pv2 / cum_v - mean * mean, 0.0)
        dev = variance**0.5
        for band in bands:
            out[f"upper_{band}"][i] = mean + band * dev
            out[f"lower_{band}"][i] = mean - band * dev

    return out
