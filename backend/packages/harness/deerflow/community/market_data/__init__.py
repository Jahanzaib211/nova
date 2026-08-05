"""Free market-data tools: OHLCV fetch, technical indicators, signal backtesting.

Data comes from yfinance (equities, FX, futures) and ccxt public endpoints
(crypto) — no API keys. Both are optional dependencies; install with
``uv sync --extra trading``.

``indicators`` is pure Python and has no dependencies at all, so
``compute_indicators`` and ``backtest_signals`` work on a default install
once bars are in hand.
"""

from __future__ import annotations

__all__ = ["indicators", "providers", "tools"]
