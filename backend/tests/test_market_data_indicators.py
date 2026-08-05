"""Indicator correctness and alignment.

The values are checked against independently-derived recursions rather than
against the implementation itself. Wilder smoothing in particular is easy to
get subtly wrong (seeding with the first value instead of the SMA of the
first window shifts the whole curve), and a wrong-but-plausible indicator is
worse than a missing one — it produces confident, unfalsifiable signals.

Alignment is tested just as hard: every series must be the same length as its
input, left-padded with None. Callers zip these back onto bars by index, so a
series that quietly starts short shifts every signal by the warm-up length.
"""

from __future__ import annotations

import random

import pytest

from deerflow.community.market_data import indicators as ind


@pytest.fixture(scope="module")
def closes() -> list[float]:
    random.seed(7)
    series = [100.0]
    for _ in range(400):
        series.append(round(series[-1] * (1 + random.gauss(0, 0.01)), 4))
    return series


@pytest.fixture(scope="module")
def ohlc(closes: list[float]) -> tuple[list[float], list[float], list[float]]:
    highs = [c * 1.004 for c in closes]
    lows = [c * 0.996 for c in closes]
    return highs, lows, closes


class TestSma:
    def test_matches_manual_mean(self, closes: list[float]) -> None:
        out = ind.sma(closes, 20)
        for i in (19, 100, 399):
            assert out[i] == pytest.approx(sum(closes[i - 19 : i + 1]) / 20)

    def test_warmup_is_none_and_length_matches(self, closes: list[float]) -> None:
        out = ind.sma(closes, 20)
        assert len(out) == len(closes)
        assert out[:19] == [None] * 19
        assert out[19] is not None

    def test_shorter_than_period_is_all_none(self) -> None:
        assert ind.sma([1, 2, 3], 10) == [None, None, None]

    def test_rejects_nonpositive_period(self) -> None:
        with pytest.raises(ValueError, match="positive"):
            ind.sma([1, 2, 3], 0)


class TestEma:
    def test_seeded_with_sma_not_first_value(self, closes: list[float]) -> None:
        """TradingView's ta.ema seeds on the SMA of the first window. Seeding
        on closes[0] gives a visibly different curve that will not reconcile
        against a chart."""
        assert ind.ema(closes, 10)[9] == pytest.approx(ind.sma(closes, 10)[9])

    def test_matches_independent_recursion(self, closes: list[float]) -> None:
        period, alpha = 10, 2 / 11
        prev = sum(closes[:period]) / period
        expected = [prev]
        for value in closes[period:]:
            prev = (value - prev) * alpha + prev
            expected.append(prev)

        got = ind.ema(closes, period)[period - 1 :]
        assert got == pytest.approx(expected)


class TestRsi:
    def test_matches_wilder_recursion(self, closes: list[float]) -> None:
        period = 14
        gains = [max(closes[i] - closes[i - 1], 0.0) for i in range(1, len(closes))]
        losses = [max(closes[i - 1] - closes[i], 0.0) for i in range(1, len(closes))]
        avg_gain = sum(gains[:period]) / period
        avg_loss = sum(losses[:period]) / period
        expected = [100.0 if avg_loss == 0 else 100 - 100 / (1 + avg_gain / avg_loss)]
        for i in range(period, len(gains)):
            avg_gain = (avg_gain * (period - 1) + gains[i]) / period
            avg_loss = (avg_loss * (period - 1) + losses[i]) / period
            expected.append(100.0 if avg_loss == 0 else 100 - 100 / (1 + avg_gain / avg_loss))

        assert ind.rsi(closes, period)[period:] == pytest.approx(expected)

    def test_bounded_zero_to_hundred(self, closes: list[float]) -> None:
        assert all(0.0 <= v <= 100.0 for v in ind.rsi(closes, 14) if v is not None)

    def test_monotonic_rise_pins_at_hundred(self) -> None:
        """No losses means avg_loss == 0, which must not divide by zero."""
        assert ind.rsi(list(range(1, 40)), 14)[-1] == 100.0

    def test_warmup_length(self, closes: list[float]) -> None:
        out = ind.rsi(closes, 14)
        assert len(out) == len(closes)
        assert out[:14] == [None] * 14


class TestAtr:
    def test_matches_wilder_recursion(self, ohlc) -> None:
        highs, lows, closes = ohlc
        period = 14
        tr = [highs[0] - lows[0]]
        for i in range(1, len(closes)):
            tr.append(max(highs[i] - lows[i], abs(highs[i] - closes[i - 1]), abs(lows[i] - closes[i - 1])))
        expected = [sum(tr[:period]) / period]
        for i in range(period, len(tr)):
            expected.append((expected[-1] * (period - 1) + tr[i]) / period)

        assert ind.atr(highs, lows, closes, period)[period - 1 :] == pytest.approx(expected)

    def test_true_range_uses_previous_close(self) -> None:
        """A gap down must register the gap, not just the bar's own range."""
        tr = ind.true_range([10, 5], [9, 4], [10, 5])
        assert tr[1] == pytest.approx(6.0)  # |4 - 10| beats the 1.0 bar range

    def test_always_non_negative(self, ohlc) -> None:
        highs, lows, closes = ohlc
        assert all(v >= 0 for v in ind.atr(highs, lows, closes, 14) if v is not None)


class TestMacd:
    def test_all_three_series_align_with_input(self, closes: list[float]) -> None:
        out = ind.macd(closes)
        assert set(out) == {"macd", "signal", "histogram"}
        assert all(len(v) == len(closes) for v in out.values())

    def test_histogram_is_macd_minus_signal(self, closes: list[float]) -> None:
        out = ind.macd(closes)
        for i in range(len(closes)):
            if out["histogram"][i] is not None:
                assert out["histogram"][i] == pytest.approx(out["macd"][i] - out["signal"][i])

    def test_signal_is_defined_later_than_macd(self, closes: list[float]) -> None:
        """The signal EMA runs over the MACD line, so it must warm up after it
        — this is where an off-by-one padding bug would show."""
        out = ind.macd(closes)
        first_macd = next(i for i, v in enumerate(out["macd"]) if v is not None)
        first_signal = next(i for i, v in enumerate(out["signal"]) if v is not None)
        assert first_signal > first_macd


class TestAdx:
    def test_series_align_and_are_bounded(self, ohlc) -> None:
        highs, lows, closes = ohlc
        out = ind.adx(highs, lows, closes, 14)
        assert set(out) == {"adx", "plus_di", "minus_di"}
        assert all(len(v) == len(closes) for v in out.values())
        for key in out:
            assert all(0.0 <= v <= 100.0 for v in out[key] if v is not None)

    def test_short_input_returns_aligned_nones(self) -> None:
        out = ind.adx([1, 2, 3], [0, 1, 2], [1, 2, 3], 14)
        assert all(len(v) == 3 and set(v) == {None} for v in out.values())

    def test_strong_uptrend_has_plus_di_above_minus_di(self) -> None:
        closes = [float(i) for i in range(1, 80)]
        highs = [c + 0.5 for c in closes]
        lows = [c - 0.5 for c in closes]
        out = ind.adx(highs, lows, closes, 14)
        assert out["plus_di"][-1] > out["minus_di"][-1]


class TestBollinger:
    def test_bands_straddle_the_basis(self, closes: list[float]) -> None:
        out = ind.bollinger(closes, 20, 2.0)
        for i in range(19, len(closes)):
            assert out["upper"][i] >= out["basis"][i] >= out["lower"][i]

    def test_basis_equals_sma(self, closes: list[float]) -> None:
        assert ind.bollinger(closes, 20, 2.0)["basis"] == ind.sma(closes, 20)

    def test_uses_population_stddev(self) -> None:
        """TradingView divides by N, not N-1. Using the sample stdev would
        widen every band and shift every touch signal."""
        values = [1.0, 2.0, 3.0, 4.0, 5.0]
        out = ind.bollinger(values, 5, 1.0)
        mean = 3.0
        population = (sum((v - mean) ** 2 for v in values) / 5) ** 0.5
        assert out["upper"][4] == pytest.approx(mean + population)


class TestSessionVwap:
    def _bars(self, days: int, per_day: int):
        highs, lows, closes, volumes, keys = [], [], [], [], []
        price = 100.0
        for day in range(days):
            for _ in range(per_day):
                price += 0.1
                highs.append(price + 0.2)
                lows.append(price - 0.2)
                closes.append(price)
                volumes.append(1000.0)
                keys.append(f"2026-07-{day + 1:02d}")
        return highs, lows, closes, volumes, keys

    def test_resets_at_each_session_boundary(self) -> None:
        """A VWAP that runs continuously across days is a different, far less
        useful indicator than the session VWAP a mean-reversion strategy
        anchors to."""
        highs, lows, closes, volumes, keys = self._bars(days=2, per_day=10)
        out = ind.session_vwap(highs, lows, closes, volumes, keys)
        first_bar_of_day_two = 10
        typical = (highs[first_bar_of_day_two] + lows[first_bar_of_day_two] + closes[first_bar_of_day_two]) / 3
        assert out["vwap"][first_bar_of_day_two] == pytest.approx(typical)

    def test_first_bar_vwap_is_its_own_typical_price(self) -> None:
        highs, lows, closes, volumes, keys = self._bars(days=1, per_day=5)
        out = ind.session_vwap(highs, lows, closes, volumes, keys)
        assert out["vwap"][0] == pytest.approx((highs[0] + lows[0] + closes[0]) / 3)

    def test_bands_widen_with_k(self) -> None:
        highs, lows, closes, volumes, keys = self._bars(days=1, per_day=30)
        out = ind.session_vwap(highs, lows, closes, volumes, keys, bands=(1.5, 2.5))
        i = -1
        assert out["upper_2.5"][i] > out["upper_1.5"][i] > out["vwap"][i]
        assert out["lower_2.5"][i] < out["lower_1.5"][i] < out["vwap"][i]

    def test_zero_volume_bars_still_produce_a_vwap(self) -> None:
        """FX/CFD feeds routinely report zero volume; dividing by it would
        make VWAP undefined for exactly the instrument this was built for."""
        highs, lows, closes, volumes, keys = self._bars(days=1, per_day=5)
        out = ind.session_vwap(highs, lows, closes, [0.0] * 5, keys)
        assert all(v is not None for v in out["vwap"])

    def test_series_align_with_input(self) -> None:
        highs, lows, closes, volumes, keys = self._bars(days=2, per_day=8)
        out = ind.session_vwap(highs, lows, closes, volumes, keys)
        assert all(len(v) == 16 for v in out.values())
