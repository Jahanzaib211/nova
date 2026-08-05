---
name: pine-script
description: Write, validate, and debug TradingView Pine Script v5/v6 indicators and strategies. Use when the user asks for a Pine script, a TradingView indicator or strategy, a .pine file, backtest code for TradingView, or help with Pine compile errors like "Undeclared identifier". Also covers loading a script into TradingView's Pine Editor via browser automation.
license: MIT
---

# Pine Script v5 / v6

## The one rule that matters

**Never invent a built-in.** Almost every wasted round-trip to TradingView
comes from an identifier that looks plausible and does not exist —
`strategy.profit_factor`, `ta.crossabove`, `ta.stddev`. TradingView reports
these as `Undeclared identifier`, and you cannot tell from the name alone.

So: after writing any `.pine` file, run the validator.

```bash
python /mnt/skills/public/pine-script/scripts/validate_pine.py yourscript.pine
```

Exit code 0 means no errors. Warnings are advisory — the validator's built-in
inventory is hand-maintained and deliberately incomplete, so it reports
unrecognised identifiers as "verify this", never as failures. Only identifiers
*proven* not to exist are errors.

To see what it knows: `validate_pine.py --list-builtins`.

## Workflow

1. Ask which version — v6 is current, v5 is still extremely common. If the
   user doesn't say, write v6 and state that you did.
2. Start from `templates/indicator.pine` or `templates/strategy.pine`.
3. Write the script. One script per file, always.
4. Run the validator. Fix every ERROR.
5. If the user wants it live on TradingView, see
   `references/tradingview-automation.md`.

## Hard constraints

**One script = one file.** Two `//@version=` pragmas or two
`strategy()`/`indicator()` calls in one file is a compile error. If you are
asked for both an indicator and a backtest, that is two files.

**No look-ahead.** `request.security()` without
`lookahead=barmerge.lookahead_off` leaks future data and produces a backtest
that cannot be traded. Default to `lookahead_off` and `gaps=barmerge.gaps_off`.

**Backtest realism.** A strategy without `commission_type`, `commission_value`,
and `slippage` will overstate its edge — for an intraday scalper the
difference routinely flips a profitable curve negative. Always set them, and
say what you assumed.

## The built-ins people get wrong

These are Strategy Tester **UI metrics**, not script variables. Reading them
is a compile error:

| Not a built-in | Compute it instead |
|---|---|
| `strategy.profit_factor` | `math.abs(strategy.grossprofit / strategy.grossloss)` |
| `strategy.avg_winning_trade` | `strategy.grossprofit / strategy.wintrades` |
| `strategy.avg_losing_trade` | `strategy.grossloss / strategy.losstrades` |
| `strategy.percent_profitable` | `strategy.wintrades / strategy.closedtrades * 100` |
| `strategy.sharpe_ratio` | no script-side equivalent |

These **are** real and readable from script code: `strategy.netprofit`,
`strategy.grossprofit`, `strategy.grossloss`, `strategy.wintrades`,
`strategy.losstrades`, `strategy.closedtrades`, `strategy.opentrades`,
`strategy.max_drawdown`, `strategy.max_runup`, `strategy.equity`,
`strategy.position_size`, `strategy.position_avg_price`.

Note `strategy.max_drawdown` is **absolute currency**, not a percentage.

## References

Load these only when the task calls for them:

- `references/v5-to-v6.md` — what changed, and how to migrate
- `references/common-errors.md` — compile errors, repainting, footguns
- `references/tradingview-automation.md` — loading a script into the Pine Editor with Nova's browser tools

## Getting real market data

Nova has a `trading` tool group for validating strategy logic against real
prices *before* committing to a TradingView round-trip: `get_ohlcv`,
`compute_indicators` (EMA/RSI/MACD/ATR/ADX/Bollinger/session-VWAP), and
`backtest_signals`. Free and keyless.

Gold has no free spot feed — use `GC=F` (COMEX futures, ~1% above spot from
carry), or `PAXG/USDT` via `source="ccxt"` for a spot-tracking series.
`XAUUSD`, `XAUUSD=X`, and `XAU=X` all return nothing.
