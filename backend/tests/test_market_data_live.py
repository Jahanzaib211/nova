"""Live market-data tests — real network, real providers, real prices.

These hit Yahoo Finance and Binance for real. They are skipped in CI and
whenever the optional ``trading`` extra is not installed:

    cd backend && uv sync --extra trading
    PYTHONPATH=. uv run pytest tests/test_market_data_live.py -v -s

They exist because the mocked suite cannot catch the failures that actually
bite here: a provider changing its response shape, an interval Yahoo quietly
refuses, or a symbol that returns an *empty frame* instead of an error.
``XAUUSD=X`` was found that way — it looks like a valid Yahoo FX symbol and
is widely cited as one, but it returns nothing, so the docstrings and error
messages were corrected to point at ``GC=F`` / ``PAXG/USDT`` instead.

Assertions are deliberately loose about price *levels* (markets move) and
strict about structure, ordering, and internal consistency.
"""

from __future__ import annotations

import json
import os
from datetime import UTC, datetime

import pytest
from support.live_gate import live_skip_reason

# Live tests are opt-in (DEERFLOW_LIVE_TESTS=1) and never run in CI.
_skip_reason = live_skip_reason()
if _skip_reason is None:
    try:
        import ccxt  # noqa: F401
        import yfinance  # noqa: F401
    except ImportError:
        _skip_reason = "trading extra not installed — run: uv sync --extra trading"

if _skip_reason:
    pytest.skip(_skip_reason, allow_module_level=True)

from deerflow.community.market_data.providers import MarketDataError, fetch_ohlcv  # noqa: E402
from deerflow.community.market_data.tools import (  # noqa: E402
    backtest_signals_tool,
    compute_indicators_tool,
    get_ohlcv_tool,
)


def _call(tool, **kwargs):
    return getattr(tool, "func", tool)(**kwargs)


def _assert_well_formed(bars: list[dict], symbol: str) -> None:
    assert bars, f"{symbol} returned no bars"
    for bar in bars:
        assert bar["high"] >= bar["low"], f"{symbol}: high < low at {bar['time']}"
        assert bar["high"] >= bar["open"] >= bar["low"], f"{symbol}: open outside range at {bar['time']}"
        assert bar["high"] >= bar["close"] >= bar["low"], f"{symbol}: close outside range at {bar['time']}"
        assert bar["volume"] >= 0
        datetime.fromisoformat(bar["time"])  # raises if not ISO8601

    times = [b["time"] for b in bars]
    assert times == sorted(times), f"{symbol}: bars are not chronologically ordered"
    assert len(set(times)) == len(times), f"{symbol}: duplicate timestamps"


class TestLiveYahoo:
    @pytest.mark.parametrize(("symbol", "interval"), [("GC=F", "15m"), ("MGC=F", "1h"), ("EURUSD=X", "1h"), ("AAPL", "1d")])
    def test_real_symbols_return_well_formed_bars(self, symbol: str, interval: str) -> None:
        bars, source = fetch_ohlcv(symbol, interval, 60)
        assert source == "yfinance"
        _assert_well_formed(bars, symbol)
        assert len(bars) > 10

    def test_gold_futures_is_in_a_sane_range(self) -> None:
        """A wide sanity band, not a price prediction — this catches a feed
        returning an index level or a different instrument entirely."""
        bars, _ = fetch_ohlcv("GC=F", "1h", 30)
        assert 500 < bars[-1]["close"] < 20000

    def test_bars_are_recent(self) -> None:
        """A stale feed is worse than a broken one — it looks like data."""
        bars, _ = fetch_ohlcv("GC=F", "1h", 30)
        age = datetime.now(UTC) - datetime.fromisoformat(bars[-1]["time"])
        assert age.days < 10, f"latest GC=F bar is {age.days} days old"

    def test_limit_is_respected(self) -> None:
        bars, _ = fetch_ohlcv("AAPL", "1d", 25)
        assert len(bars) <= 25

    def test_spot_gold_symbols_fail_with_the_documented_workaround(self) -> None:
        """Yahoo returns an *empty frame* for these rather than an error, so
        without the hint this is indistinguishable from a closed market."""
        for bad in ("XAUUSD=X", "XAU=X", "XAUUSD"):
            with pytest.raises(MarketDataError) as excinfo:
                fetch_ohlcv(bad, "1h", 10)
            message = str(excinfo.value)
            assert "GC=F" in message
            assert "PAXG/USDT" in message

    def test_fx_pair_without_suffix_is_diagnosed(self) -> None:
        with pytest.raises(MarketDataError, match=r"EURUSD=X"):
            fetch_ohlcv("EURUSD", "1h", 10)


class TestLiveCcxt:
    @pytest.mark.parametrize("symbol", ["BTC/USDT", "ETH/USDT", "PAXG/USDT"])
    def test_real_pairs_return_well_formed_bars(self, symbol: str) -> None:
        bars, source = fetch_ohlcv(symbol, "15m", 50)
        assert source == "ccxt:binance"
        _assert_well_formed(bars, symbol)
        assert len(bars) == 50

    def test_auto_routing_sends_pairs_to_ccxt(self) -> None:
        _, source = fetch_ohlcv("BTC/USDT", "1h", 10, source="auto")
        assert source.startswith("ccxt")

    def test_tokenized_gold_tracks_futures_within_a_few_percent(self) -> None:
        """PAXG is the documented spot-gold workaround, so the claim that it
        tracks gold is worth actually checking rather than asserting."""
        paxg, _ = fetch_ohlcv("PAXG/USDT", "1h", 5)
        gold, _ = fetch_ohlcv("GC=F", "1h", 5)
        spread = abs(paxg[-1]["close"] - gold[-1]["close"]) / gold[-1]["close"]
        assert spread < 0.05, f"PAXG {paxg[-1]['close']} vs GC=F {gold[-1]['close']} — {spread:.1%} apart"

    def test_unknown_pair_is_reported_not_silently_empty(self) -> None:
        with pytest.raises(MarketDataError):
            fetch_ohlcv("NOTACOIN/USDT", "1h", 10)


class TestLiveToolSurface:
    def test_get_ohlcv_returns_parseable_json(self) -> None:
        payload = json.loads(_call(get_ohlcv_tool, description="live", symbol="GC=F", interval="15m", limit=40))
        assert payload["source"] == "yfinance"
        assert payload["count"] == len(payload["bars"]) <= 40
        assert payload["latest"] == payload["bars"][-1]

    def test_compute_indicators_over_real_gold(self) -> None:
        payload = json.loads(
            _call(
                compute_indicators_tool,
                description="live",
                symbol="GC=F",
                interval="15m",
                limit=200,
                studies="ema,rsi,macd,atr,adx,bollinger,vwap",
                tail=3,
            )
        )
        studies = payload["studies"]
        assert "unknown_studies" not in payload

        # Every requested series must be present and warmed up on 200 bars.
        for key in ("ema_9", "ema_21", "ema_50", "rsi_14", "atr_14", "adx_adx", "bb_upper", "vwap"):
            assert key in studies, f"missing {key}"
            assert studies[key][-1] is not None, f"{key} did not warm up over 200 bars"

        assert 0 <= studies["rsi_14"][-1] <= 100
        assert studies["atr_14"][-1] > 0
        assert studies["bb_upper"][-1] > studies["bb_basis"][-1] > studies["bb_lower"][-1]

        # VWAP must sit inside the day's actual range, not float off somewhere.
        assert studies["vwap_upper_1.5"][-1] > studies["vwap"][-1] > studies["vwap_lower_1.5"][-1]

    def test_indicators_reconcile_with_the_raw_bars(self) -> None:
        """The same fetch through two tools must agree — this catches an
        alignment bug where a series is silently shifted from its bars."""
        raw = json.loads(_call(get_ohlcv_tool, description="live", symbol="BTC/USDT", interval="1h", limit=100))
        computed = json.loads(_call(compute_indicators_tool, description="live", symbol="BTC/USDT", interval="1h", limit=100, studies="sma", tail=1))
        # Prices move between calls; assert they agree to within a percent.
        assert abs(computed["last_close"] - raw["latest"]["close"]) / raw["latest"]["close"] < 0.01

        closes = [b["close"] for b in raw["bars"]]
        expected_sma20 = sum(closes[-20:]) / 20
        assert computed["studies"]["sma_20"][-1] == pytest.approx(expected_sma20, rel=0.01)

    def test_backtest_over_real_candles(self) -> None:
        raw = json.loads(_call(get_ohlcv_tool, description="live", symbol="BTC/USDT", interval="1h", limit=200))
        bars = raw["bars"]

        # Build signals off real bars: enter at each 20th close with a
        # symmetric 1R stop / 2R target sized from the bar's own range.
        signals = []
        for i in range(20, 120, 20):
            entry = bars[i]["close"]
            risk = max(bars[i]["high"] - bars[i]["low"], entry * 0.002)
            signals.append({"time": bars[i]["time"], "side": "long", "entry": entry, "stop": entry - risk, "target": entry + 2 * risk})

        payload = json.loads(_call(backtest_signals_tool, description="live", symbol="BTC/USDT", signals=json.dumps(signals), interval="1h", limit=200))
        assert payload["signals_evaluated"] == len(signals)
        assert payload["skipped"] == []
        assert payload["wins"] + payload["losses"] + payload["still_open"] == len(signals)

        # Internal consistency of the aggregates.
        assert payload["total_r"] == pytest.approx(sum(t["r_multiple"] for t in payload["trades"]), abs=1e-3)
        assert payload["expectancy_r"] == pytest.approx(payload["total_r"] / len(signals), abs=1e-3)
        if payload["closed_trades"]:
            assert payload["win_rate_pct"] == pytest.approx(100 * payload["wins"] / payload["closed_trades"], abs=0.01)
        for trade in payload["trades"]:
            assert trade["outcome"] in {"win", "loss", "open"}
            assert trade["planned_rr"] == pytest.approx(2.0, rel=0.01)
