# Pine Script v5 → v6

## Declaring the version

```pine
//@version=6
```

Must be the first line. Without a pragma TradingView compiles as v1, which
fails on essentially any modern script with errors that point nowhere useful.

## What actually changed

### `var` declarations want explicit types

v6 tightened inference on `var`. Untyped declarations still often compile, but
they produce confusing type errors later when the variable is assigned a
different numeric type.

```pine
// v5 — fine
var counter = 0
var peak = 0.0

// v6 — preferred
var int   counter = 0
var float peak    = 0.0
```

### `alertcondition()` → `alert()`

`alertcondition()` still compiles in v6, so this is a preference, not a fix.
The reason to move: `alertcondition()` requires a *compile-time constant*
message, while `alert()` builds its message at runtime, so you can include
live prices and levels.

```pine
// v5 style — message is frozen at compile time
alertcondition(longSignal, title="Long", message="Long signal")

// v6 style — message computed per bar
if longSignal
    alert("LONG " + syminfo.ticker + " @ " + str.tostring(close, format.mintick) +
          " SL " + str.tostring(stopPrice, format.mintick), alert.freq_once_per_bar_close)
```

### `request.security()` — always pin lookahead

Unchanged syntactically, but v6 is the moment to fix the defaults. Without
`lookahead_off`, a higher-timeframe call returns values that were not known
at the time — the backtest looks superb and cannot be traded.

```pine
htfEma = request.security(syminfo.tickerid, "60", ta.ema(close, 21),
                          lookahead = barmerge.lookahead_off,
                          gaps      = barmerge.gaps_off)
```

### `input.timeframe()` wants a real timeframe string

```pine
srcTf = input.timeframe("5", "Source timeframe")   // "5", "15", "60", "D"
```

Not `"5m"`, not `"M5"`. Pine timeframe strings are bare minute counts, or
`D`/`W`/`M`.

## Migrating from v4 or earlier

v4 used bare function names. v5 moved them into namespaces, and that is the
single biggest source of migration errors:

| v4 | v5 / v6 |
|---|---|
| `rsi(close, 14)` | `ta.rsi(close, 14)` |
| `sma(close, 20)` | `ta.sma(close, 20)` |
| `ema(close, 21)` | `ta.ema(close, 21)` |
| `atr(14)` | `ta.atr(14)` |
| `crossover(a, b)` | `ta.crossover(a, b)` |
| `highest(high, 20)` | `ta.highest(high, 20)` |
| `valuewhen(c, s, n)` | `ta.valuewhen(c, s, n)` |
| `security(...)` | `request.security(...)` |
| `abs(x)`, `max(a,b)` | `math.abs(x)`, `math.max(a,b)` |
| `tostring(x)` | `str.tostring(x)` |
| `iff(c, a, b)` | `c ? a : b` |
| `study(...)` | `indicator(...)` |

The validator flags all of these as errors when it sees them in a v5+ script.

## Type system notes

`simple` vs `series` still bites. Function parameters declared `simple int`
cannot take a value that varies per bar. If you get *"Cannot call 'ta.ema'
with argument 'length'... series[int] but expected simple[int]"*, the length
is coming from something bar-dependent — hoist it to an `input.int()` or a
`var`.
