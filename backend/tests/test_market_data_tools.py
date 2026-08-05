"""Tool-level tests for the trading group.

External market data is faked by injecting stub ``yfinance`` / ``ccxt``
modules into ``sys.modules`` (the idiom from test_ddg_search_tools.py), so
nothing here touches the network.

The tools are invoked through ``.func`` to bypass LangChain's arg-schema
validation and exercise the implementation directly.
"""

from __future__ import annotations

import json
import sys
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest

from deerflow.community.market_data import providers
from deerflow.community.market_data.providers import MarketDataError, fetch_ohlcv, looks_like_crypto_pair, normalize_interval
from deerflow.community.market_data.tools import (
    backtest_signals_tool,
    compute_indicators_tool,
    get_ohlcv_tool,
)


def _call(tool, **kwargs):
    return getattr(tool, "func", tool)(**kwargs)


def _bars(count: int = 60, start: float = 4000.0, step: float = 1.0) -> list[dict]:
    base = datetime(2026, 7, 30, tzinfo=UTC)
    out = []
    for i in range(count):
        price = start + i * step
        out.append(
            {
                "time": (base + timedelta(minutes=15 * i)).isoformat(),
                "open": price,
                "high": price + 2,
                "low": price - 2,
                "close": price,
                "volume": 1000.0,
            }
        )
    return out


@pytest.fixture
def stub_bars(monkeypatch: pytest.MonkeyPatch):
    """Replace the provider layer so tools are tested without network."""
    captured: dict = {}

    def fake_fetch(symbol, interval="15m", limit=200, source="auto", exchange="binance"):
        captured.update(symbol=symbol, interval=interval, limit=limit, source=source, exchange=exchange)
        return _bars(limit if limit <= 200 else 200), "stub"

    monkeypatch.setattr("deerflow.community.market_data.tools.fetch_ohlcv", fake_fetch)
    return captured


class TestIntervalNormalization:
    @pytest.mark.parametrize(
        ("given", "expected"),
        [("1min", "1m"), ("15min", "15m"), ("1hour", "1h"), ("60m", "1h"), ("daily", "1d"), ("15m", "15m")],
    )
    def test_aliases(self, given: str, expected: str) -> None:
        assert normalize_interval(given) == expected

    def test_crypto_pair_detection(self) -> None:
        assert looks_like_crypto_pair("BTC/USDT") is True
        assert looks_like_crypto_pair("GC=F") is False


class TestGetOhlcv:
    def test_returns_normalized_bars(self, stub_bars) -> None:
        payload = json.loads(_call(get_ohlcv_tool, description="d", symbol="GC=F", interval="15m", limit=50))
        assert payload["symbol"] == "GC=F"
        assert payload["count"] == 50
        assert payload["source"] == "stub"
        assert set(payload["bars"][0]) == {"time", "open", "high", "low", "close", "volume"}
        assert payload["latest"] == payload["bars"][-1]

    def test_limit_is_clamped(self, stub_bars) -> None:
        _call(get_ohlcv_tool, description="d", symbol="GC=F", limit=99999)
        assert stub_bars["limit"] == 1000

    def test_limit_floor(self, stub_bars) -> None:
        _call(get_ohlcv_tool, description="d", symbol="GC=F", limit=0)
        assert stub_bars["limit"] == 1

    def test_provider_error_is_returned_not_raised(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Tools return 'Error: ...' strings so the agent can adapt, rather
        than aborting the run."""

        def boom(*args, **kwargs):
            raise MarketDataError("no data returned for 'XAUUSD'. Check the symbol (gold is 'GC=F' ...)")

        monkeypatch.setattr("deerflow.community.market_data.tools.fetch_ohlcv", boom)
        result = _call(get_ohlcv_tool, description="d", symbol="XAUUSD")
        assert result.startswith("Error:")
        assert "GC=F" in result


class TestComputeIndicators:
    def test_default_studies(self, stub_bars) -> None:
        payload = json.loads(_call(compute_indicators_tool, description="d", symbol="GC=F", limit=100))
        studies = payload["studies"]
        assert {"ema_9", "ema_21", "rsi_14", "atr_14", "vwap"} <= set(studies)
        assert payload["bars_used"] == 100
        assert payload["last_close"] is not None

    def test_tail_limits_series_length(self, stub_bars) -> None:
        payload = json.loads(_call(compute_indicators_tool, description="d", symbol="GC=F", limit=100, studies="rsi", tail=3))
        assert len(payload["studies"]["rsi_14"]) == 3

    def test_all_studies_resolve(self, stub_bars) -> None:
        payload = json.loads(
            _call(
                compute_indicators_tool,
                description="d",
                symbol="GC=F",
                limit=200,
                studies="sma,ema,rsi,macd,atr,adx,bollinger,vwap",
            )
        )
        assert "unknown_studies" not in payload
        assert {"sma_20", "macd_macd", "adx_adx", "bb_upper", "vwap_upper_1.5"} <= set(payload["studies"])

    def test_unknown_study_is_reported_with_the_valid_list(self, stub_bars) -> None:
        payload = json.loads(_call(compute_indicators_tool, description="d", symbol="GC=F", studies="rsi,supertrend"))
        assert payload["unknown_studies"] == ["supertrend"]
        assert "bollinger" in payload["available_studies"]
        assert "rsi_14" in payload["studies"]


class TestBacktestSignals:
    def _signal(self, index: int, side: str, entry: float, stop: float, target: float) -> dict:
        return {"time": _bars()[index]["time"], "side": side, "entry": entry, "stop": stop, "target": target}

    def test_winning_long_scores_planned_r(self, stub_bars) -> None:
        # Bars rise 1.0/bar from 4000 with ±2 wicks; a long targeting +10 wins.
        signals = [self._signal(0, "long", 4000.0, 3995.0, 4010.0)]
        payload = json.loads(_call(backtest_signals_tool, description="d", symbol="GC=F", signals=json.dumps(signals), limit=60))
        assert payload["wins"] == 1
        assert payload["losses"] == 0
        assert payload["win_rate_pct"] == 100.0
        assert payload["trades"][0]["r_multiple"] == pytest.approx(2.0)

    def test_losing_short_in_a_rising_market(self, stub_bars) -> None:
        signals = [self._signal(0, "short", 4000.0, 4005.0, 3990.0)]
        payload = json.loads(_call(backtest_signals_tool, description="d", symbol="GC=F", signals=json.dumps(signals), limit=60))
        assert payload["losses"] == 1
        assert payload["trades"][0]["r_multiple"] == -1.0

    def test_ambiguous_bar_resolves_as_a_loss(self, stub_bars) -> None:
        """Stop and target both inside one bar's range. OHLC cannot reveal
        intrabar order; assuming the favourable one manufactures fake edge."""
        signals = [self._signal(0, "long", 4000.0, 3999.0, 4001.0)]
        payload = json.loads(_call(backtest_signals_tool, description="d", symbol="GC=F", signals=json.dumps(signals), limit=60))
        assert payload["losses"] == 1
        assert payload["ambiguous_bars"] == 1
        assert payload["trades"][0]["ambiguous_bar"] is True

    def test_unresolved_signal_is_counted_as_open(self, stub_bars) -> None:
        signals = [self._signal(55, "long", 4055.0, 3000.0, 9000.0)]
        payload = json.loads(_call(backtest_signals_tool, description="d", symbol="GC=F", signals=json.dumps(signals), limit=60))
        assert payload["still_open"] == 1
        assert payload["closed_trades"] == 0
        assert payload["win_rate_pct"] is None

    def test_aggregates_across_signals(self, stub_bars) -> None:
        signals = [
            self._signal(0, "long", 4000.0, 3995.0, 4010.0),
            self._signal(5, "short", 4005.0, 4010.0, 3990.0),
            self._signal(10, "long", 4010.0, 4005.0, 4020.0),
        ]
        payload = json.loads(_call(backtest_signals_tool, description="d", symbol="GC=F", signals=json.dumps(signals), limit=60))
        assert payload["signals_evaluated"] == 3
        assert payload["wins"] == 2
        assert payload["losses"] == 1
        assert payload["profit_factor"] == pytest.approx(4.0)
        assert payload["max_consecutive_losses"] == 1
        assert payload["total_r"] == pytest.approx(3.0)

    def test_malformed_json_is_rejected_with_the_expected_shape(self, stub_bars) -> None:
        result = _call(backtest_signals_tool, description="d", symbol="GC=F", signals="not json")
        assert result.startswith("Error:")
        assert "time" in result and "target" in result

    def test_empty_array_is_rejected(self, stub_bars) -> None:
        assert _call(backtest_signals_tool, description="d", symbol="GC=F", signals="[]").startswith("Error:")

    def test_bad_signals_are_skipped_with_reasons_not_dropped(self, stub_bars) -> None:
        signals = [
            {"time": _bars()[0]["time"], "side": "long", "entry": 4000.0, "stop": 4000.0, "target": 4010.0},
            {"side": "long", "entry": 1},
            self._signal(0, "long", 4000.0, 3995.0, 4010.0),
        ]
        payload = json.loads(_call(backtest_signals_tool, description="d", symbol="GC=F", signals=json.dumps(signals), limit=60))
        assert payload["signals_evaluated"] == 1
        reasons = " ".join(s["reason"] for s in payload["skipped"])
        assert "zero risk" in reasons
        assert "missing/invalid field" in reasons

    def test_signal_after_last_candle_is_skipped(self, stub_bars) -> None:
        signals = [{"time": "2099-01-01T00:00:00+00:00", "side": "long", "entry": 1, "stop": 0.5, "target": 2}]
        payload = json.loads(_call(backtest_signals_tool, description="d", symbol="GC=F", signals=json.dumps(signals), limit=60))
        assert "after the last available candle" in payload["skipped"][0]["reason"]


class _FakeFrame:
    def __init__(self, rows: list[tuple]) -> None:
        self._rows = rows
        self.empty = not rows

    def iterrows(self):
        for ts, values in self._rows:
            yield SimpleNamespace(to_pydatetime=lambda t=ts: t), values


class TestYfinanceProvider:
    def test_normalizes_frame_rows(self, monkeypatch: pytest.MonkeyPatch) -> None:
        rows = [(datetime(2026, 7, 30, 9, 0, tzinfo=UTC), {"Open": 1.0, "High": 2.0, "Low": 0.5, "Close": 1.5, "Volume": 10})]
        fake = SimpleNamespace(Ticker=lambda symbol: SimpleNamespace(history=lambda **kw: _FakeFrame(rows)))
        monkeypatch.setitem(sys.modules, "yfinance", fake)

        bars = providers.fetch_yfinance("GC=F", "15m", 10)
        assert bars == [{"time": "2026-07-30T09:00:00+00:00", "open": 1.0, "high": 2.0, "low": 0.5, "close": 1.5, "volume": 10.0}]

    def test_empty_frame_names_the_symbol_convention(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """The most common user error is 'XAUUSD'. The error should say so
        instead of just reporting no data."""
        fake = SimpleNamespace(Ticker=lambda symbol: SimpleNamespace(history=lambda **kw: _FakeFrame([])))
        monkeypatch.setitem(sys.modules, "yfinance", fake)
        with pytest.raises(MarketDataError, match="GC=F"):
            providers.fetch_yfinance("XAUUSD", "15m", 10)

    def test_unsupported_interval_is_rejected_before_the_call(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setitem(sys.modules, "yfinance", SimpleNamespace(Ticker=lambda s: None))
        with pytest.raises(MarketDataError, match="4h"):
            providers.fetch_yfinance("GC=F", "3m", 10)

    def test_intraday_period_is_clamped_to_yahoo_limits(self) -> None:
        """Asking for more 1m history than Yahoo serves returns an empty frame,
        which is indistinguishable from a bad symbol — so clamp instead."""
        assert providers._yf_period_for("1m", 10000) == "7d"


class TestCcxtProvider:
    def _exchange(self, rows, timeframes=None):
        return SimpleNamespace(
            timeframes=timeframes if timeframes is not None else {"15m": "15m"},
            fetch_ohlcv=lambda symbol, timeframe, limit: rows,
            close=lambda: None,
        )

    def test_normalizes_ohlcv_rows(self, monkeypatch: pytest.MonkeyPatch) -> None:
        rows = [[1785000000000, 1.0, 2.0, 0.5, 1.5, 10.0]]
        monkeypatch.setitem(sys.modules, "ccxt", SimpleNamespace(binance=lambda cfg: self._exchange(rows)))
        bars = providers.fetch_ccxt("BTC/USDT", "15m", 10)
        assert bars[0]["open"] == 1.0
        assert bars[0]["volume"] == 10.0
        assert bars[0]["time"].endswith("+00:00")

    def test_unknown_exchange_is_actionable(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setitem(sys.modules, "ccxt", SimpleNamespace())
        with pytest.raises(MarketDataError, match="unknown ccxt exchange"):
            providers.fetch_ccxt("BTC/USDT", "15m", 10, "notanexchange")

    def test_unsupported_timeframe_lists_alternatives(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setitem(sys.modules, "ccxt", SimpleNamespace(binance=lambda cfg: self._exchange([], {"1h": "1h"})))
        with pytest.raises(MarketDataError, match="does not offer timeframe"):
            providers.fetch_ccxt("BTC/USDT", "15m", 10)


class TestSourceRouting:
    def test_pairs_route_to_ccxt_and_tickers_to_yahoo(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(providers, "fetch_ccxt", lambda *a, **k: [{"tag": "ccxt"}])
        monkeypatch.setattr(providers, "fetch_yfinance", lambda *a, **k: [{"tag": "yf"}])

        assert fetch_ohlcv("BTC/USDT", source="auto")[1].startswith("ccxt")
        assert fetch_ohlcv("GC=F", source="auto")[1] == "yfinance"

    def test_explicit_source_overrides_the_heuristic(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(providers, "fetch_yfinance", lambda *a, **k: [{"tag": "yf"}])
        assert fetch_ohlcv("BTC/USDT", source="yfinance")[1] == "yfinance"

    def test_unknown_source_is_rejected(self) -> None:
        with pytest.raises(MarketDataError, match="unknown source"):
            fetch_ohlcv("GC=F", source="bloomberg")


class TestMissingDependencyHint:
    def test_missing_package_names_the_install_command(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """A default install has no yfinance; the failure must tell the user
        exactly how to fix it rather than surfacing a raw ImportError."""
        monkeypatch.setitem(sys.modules, "yfinance", None)
        monkeypatch.delitem(sys.modules, "yfinance")

        real_import = __builtins__["__import__"] if isinstance(__builtins__, dict) else __builtins__.__import__

        def blocked(name, *args, **kwargs):
            if name == "yfinance":
                raise ImportError("No module named 'yfinance'")
            return real_import(name, *args, **kwargs)

        monkeypatch.setattr("builtins.__import__", blocked)
        with pytest.raises(MarketDataError, match="uv sync --extra trading"):
            providers.fetch_yfinance("GC=F", "15m", 10)
