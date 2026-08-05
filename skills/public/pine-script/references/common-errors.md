# Pine Script compile errors and footguns

## `Undeclared identifier '<name>'`

The single most common failure. Causes, in order of likelihood:

1. **The built-in doesn't exist.** Especially Strategy Tester metrics —
   `strategy.profit_factor`, `strategy.avg_winning_trade`,
   `strategy.avg_losing_trade`, `strategy.percent_profitable`,
   `strategy.sharpe_ratio` are all UI-only. See SKILL.md for the
   compute-it-yourself formulas.
2. **v4 name in a v5+ script** — `rsi()` instead of `ta.rsi()`.
3. **Typo in a real name** — `ta.stddev` (real one is `ta.stdev`),
   `ta.crossabove` (real one is `ta.crossover`).
4. **Used before assignment** — Pine is single-pass; a variable must be
   assigned on a line above its first use.

Run the validator; it catches every one of these except pure typos it hasn't
been taught.

## `Cannot call 'X' with argument 'length'... series[int] but expected simple[int]`

A length or lookback parameter is varying per bar. Most built-ins require the
length to be fixed for the whole script. Hoist it:

```pine
// Fails — length depends on a per-bar value
len = volatile ? 10 : 20
e   = ta.ema(close, len)

// Works — compute both, select the result
e = volatile ? ta.ema(close, 10) : ta.ema(close, 20)
```

## `Script could not be translated from: null`

Almost always a stray character, an unclosed bracket, or mixed
tabs/spaces. The validator's bracket check catches the structural cases.

## Repainting

A script "repaints" when its historical signals differ from what it showed
live. Three causes:

**1. Higher-timeframe lookahead.** Always:

```pine
request.security(syminfo.tickerid, "60", expr,
                 lookahead = barmerge.lookahead_off,
                 gaps      = barmerge.gaps_off)
```

**2. Acting on an unconfirmed bar.** `close` on the current bar changes until
the bar closes. Gate signals on `barstate.isconfirmed`, or accept that live
signals will flicker:

```pine
if longCondition and barstate.isconfirmed
    strategy.entry("L", strategy.long)
```

**repaint check:** `ta.pivothigh(n, m)` is only knowable `m` bars *after* the
pivot. It is not repainting — but the signal is inherently delayed by `m`
bars, and a backtest that ignores that delay is wrong.

**3. `calc_on_every_tick=true`** in `strategy()` makes live behaviour diverge
from historical. Leave it false unless you specifically want tick-level
simulation.

## Backtest results that can't be reproduced live

Set these explicitly. Omitting them is the most common reason a backtest
looks profitable and live trading isn't:

```pine
strategy("Name", overlay = true,
         initial_capital      = 10000,
         default_qty_type     = strategy.fixed,
         default_qty_value    = 1,
         commission_type      = strategy.commission.cash_per_contract,
         commission_value     = 0.5,
         slippage             = 2,
         pyramiding           = 0,
         process_orders_on_close = false,
         calc_on_every_tick   = false)
```

`slippage` is in **ticks**, not currency. `process_orders_on_close=true`
fills at the signal bar's close, which is optimistic — it assumes you saw the
close and traded at it simultaneously.

## Variable shadowing across merged scripts

Pine permits re-declaring a name. The later declaration wins, silently, and
the earlier block reads a value it never computed. This bites hardest when
two scripts are pasted into one file — which is also a compile error for a
different reason (two `strategy()` calls).

Common collision names: `rsi`, `atr`, `adx`, `qty`, `stopDist`, `entryPx`,
`peakEquity`. The validator warns on repeated `var` declarations.

## Manual metric recipes

Since the UI metrics aren't readable, compute them:

```pine
profitFactor = strategy.grossloss != 0 ? math.abs(strategy.grossprofit / strategy.grossloss) : na
avgWin       = strategy.wintrades  > 0 ? strategy.grossprofit / strategy.wintrades  : na
avgLoss      = strategy.losstrades > 0 ? strategy.grossloss   / strategy.losstrades : na
winRate      = strategy.closedtrades > 0 ? strategy.wintrades / strategy.closedtrades * 100 : na
expectancy   = strategy.closedtrades > 0 ? strategy.netprofit / strategy.closedtrades : na

// Peak-to-trough drawdown, tracked manually
var float peakEquity = 0.0
peakEquity := math.max(peakEquity, strategy.equity)
drawdown   = peakEquity - strategy.equity
```

Guard every division — `strategy.grossloss` is 0 until the first losing trade,
and `x / 0` yields `na` that then propagates silently through the table.
