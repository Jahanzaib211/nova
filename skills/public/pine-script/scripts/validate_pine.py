#!/usr/bin/env python3
"""Static validator for TradingView Pine Script v5/v6.

Catches the failure modes that cost a round-trip to TradingView: invented
built-ins, duplicate script declarations, unbalanced brackets, and v6
migration slips.

Design rule: **never hard-fail on something merely unrecognised.** The
built-in inventory here is hand-maintained and definitely incomplete, so an
identifier that isn't in it is reported as a WARNING ("I don't recognise
this — verify it") and never as an ERROR. Errors are reserved for things
proven wrong: a missing version pragma, two `strategy()` calls in one file,
or an identifier confirmed not to exist.

A validator that confidently rejects valid code is worse than no validator,
because it trains you to ignore it.

Usage:
    python validate_pine.py strategy.pine [more.pine ...]
    python validate_pine.py --list-builtins
    cat script.pine | python validate_pine.py -

Exit codes: 0 = no errors (warnings allowed), 1 = errors found, 2 = bad usage.
"""

from __future__ import annotations

import argparse
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path

# ---------------------------------------------------------------------------
# Built-in inventory
# ---------------------------------------------------------------------------
# Namespaced built-ins. Incomplete by construction — see the design rule above.

TA = {
    "alma", "atr", "barssince", "bb", "bbw", "cci", "change", "cmo", "cog", "correlation", "cross", "crossover",
    "crossunder", "cum", "dev", "dmi", "ema", "falling", "highest", "highestbars", "hma", "kc", "kcw", "linreg",
    "lowest", "lowestbars", "macd", "max", "median", "mfi", "min", "mode", "mom", "percentile_linear_interpolation",
    "percentile_nearest_rank", "percentrank", "pivothigh", "pivotlow", "range", "rising", "rma", "roc", "rsi",
    "sar", "sma", "stdev", "stoch", "supertrend", "swma", "tr", "tsi", "valuewhen", "variance", "vwap", "vwma",
    "wma", "wpr",
}

MATH = {
    "abs", "acos", "asin", "atan", "avg", "ceil", "cos", "e", "exp", "floor", "log", "log10", "max", "min",
    "phi", "pi", "pow", "random", "round", "round_to_mintick", "rphi", "sign", "sin", "sqrt", "sum", "tan",
    "todegrees", "toradians",
}

# Variables/functions genuinely readable from strategy script code.
STRATEGY = {
    "account_currency", "avg_trade", "cancel", "cancel_all", "close", "closedtrades", "commission", "long",
    "short", "direction", "entry", "eventrades", "equity", "exit", "grossloss", "grossprofit", "initial_capital",
    "islong", "isshort", "isflat", "losstrades", "margin_liquidation_price", "max_contracts_held_all",
    "max_contracts_held_long", "max_contracts_held_short", "max_drawdown", "max_runup", "netprofit",
    "openprofit", "opentrades", "order", "position_avg_price", "position_entry_name", "position_size",
    "risk", "wintrades", "close_all", "convert_to_account", "convert_to_symbol", "default_entry_qty",
    "fixed", "cash", "percent_of_equity", "oca", "opentrades", "closedtrades",
}

# Identifiers that look plausible and are NOT built-ins. Every one here has
# been observed causing a real "Undeclared identifier" compile error — these
# are the only namespaced identifiers reported as ERRORS.
KNOWN_INVALID = {
    "strategy.profit_factor": "not a built-in (Strategy Tester UI metric only). Compute it: math.abs(strategy.grossprofit / strategy.grossloss)",
    "strategy.avg_winning_trade": "not a built-in. Compute it: strategy.grossprofit / strategy.wintrades",
    "strategy.avg_losing_trade": "not a built-in. Compute it: strategy.grossloss / strategy.losstrades",
    "strategy.percent_profitable": "not a built-in. Compute it: strategy.wintrades / strategy.closedtrades * 100",
    "strategy.sharpe_ratio": "not a built-in (Strategy Tester UI metric only); there is no script-side equivalent",
    "strategy.sortino_ratio": "not a built-in (Strategy Tester UI metric only)",
    "strategy.max_drawdown_percent": "not a built-in. strategy.max_drawdown is absolute; divide by strategy.equity yourself",
    "ta.crossabove": "no such function — use ta.crossover()",
    "ta.crossbelow": "no such function — use ta.crossunder()",
    "ta.average": "no such function — use ta.sma() or math.avg()",
    "ta.stddev": "no such function — use ta.stdev() (one 'd')",
    "ta.atr14": "not a built-in — call ta.atr(14)",
    "math.average": "no such function — use math.avg()",
}

REQUEST = {"security", "security_lower_tf", "dividends", "earnings", "splits", "financial", "quandl", "economic", "seed", "currency_rate"}
INPUT = {"bool", "color", "float", "int", "price", "session", "source", "string", "symbol", "text_area", "time", "timeframe", "enum"}
STR_NS = {"contains", "endswith", "format", "format_time", "length", "lower", "match", "pos", "replace", "replace_all", "split", "startswith", "substring", "tonumber", "tostring", "trim", "upper", "repeat"}
ARRAY = {
    "abs", "avg", "binary_search", "binary_search_leftmost", "binary_search_rightmost", "clear", "concat", "copy",
    "covariance", "every", "fill", "first", "from", "get", "includes", "indexof", "insert", "join", "last",
    "lastindexof", "max", "median", "min", "mode", "new_bool", "new_box", "new_color", "new_float", "new_int",
    "new_label", "new_line", "new_string", "new_table", "percentile_linear_interpolation", "percentile_nearest_rank",
    "percentrank", "pop", "push", "range", "remove", "reverse", "set", "shift", "size", "slice", "some", "sort",
    "sort_indices", "standardize", "stdev", "sum", "unshift", "variance", "new",
}

NAMESPACES: dict[str, set[str]] = {
    "ta": TA,
    "math": MATH,
    "strategy": STRATEGY,
    "request": REQUEST,
    "input": INPUT,
    "str": STR_NS,
    "array": ARRAY,
}

# Namespaces we deliberately do not police: their surfaces are large, highly
# versioned, and mostly cosmetic, so unknown-member warnings would be noise.
UNPOLICED = {"color", "label", "line", "box", "table", "plot", "shape", "location", "size", "position", "extend", "xloc", "yloc", "display", "format", "scale", "barmerge", "session", "adjustment", "alert", "order", "currency", "dayofweek", "text", "font", "matrix", "map", "chart", "syminfo", "timeframe", "ticker", "runtime", "log", "polyline", "linefill"}

V6_DEPRECATED = {
    "alertcondition": "still compiles in v6 but is superseded by alert() — alert() fires dynamically and carries a message built at runtime",
    "iff": "removed after v4 — use the ternary operator: cond ? a : b",
    "security": "bare security() was v4 — use request.security()",
    "rsi": "bare rsi() was v4 — use ta.rsi()",
    "sma": "bare sma() was v4 — use ta.sma()",
    "ema": "bare ema() was v4 — use ta.ema()",
    "atr": "bare atr() was v4 — use ta.atr()",
    "crossover": "bare crossover() was v4 — use ta.crossover()",
    "crossunder": "bare crossunder() was v4 — use ta.crossunder()",
    "highest": "bare highest() was v4 — use ta.highest()",
    "lowest": "bare lowest() was v4 — use ta.lowest()",
    "valuewhen": "bare valuewhen() was v4 — use ta.valuewhen()",
    "barssince": "bare barssince() was v4 — use ta.barssince()",
}

DECLARATION_RE = re.compile(r"^\s*(strategy|indicator|library)\s*\(", re.MULTILINE)
VERSION_RE = re.compile(r"^\s*//\s*@version\s*=\s*(\d+)\s*$", re.MULTILINE)
NAMESPACED_RE = re.compile(r"\b([a-z_]+)\.([a-zA-Z_][a-zA-Z0-9_]*)")
VAR_DECL_RE = re.compile(r"^\s*var(?:ip)?\s+(?:(bool|int|float|string|color|line|label|box|table|array|matrix|map)\s+)?([a-zA-Z_][a-zA-Z0-9_]*)\s*=")
ASSIGN_RE = re.compile(r"^\s*(?:var(?:ip)?\s+)?(?:(?:bool|int|float|string|color|simple|series|const)\s+)?([a-zA-Z_][a-zA-Z0-9_]*)\s*=(?!=)")

SEVERITY_ORDER = {"ERROR": 0, "WARNING": 1, "INFO": 2}


@dataclass
class Finding:
    severity: str
    line: int
    message: str
    hint: str = ""


@dataclass
class Report:
    path: str
    findings: list[Finding] = field(default_factory=list)

    def add(self, severity: str, line: int, message: str, hint: str = "") -> None:
        self.findings.append(Finding(severity, line, message, hint))

    @property
    def errors(self) -> int:
        return sum(1 for f in self.findings if f.severity == "ERROR")

    @property
    def warnings(self) -> int:
        return sum(1 for f in self.findings if f.severity == "WARNING")


def _strip_noise(source: str) -> str:
    """Blank out string literals and comments so scanning doesn't match inside them.

    Length is preserved so line/column offsets stay valid.
    """
    out = list(source)
    i = 0
    n = len(source)
    while i < n:
        ch = source[i]
        if ch in "\"'":
            quote = ch
            i += 1
            while i < n and source[i] != quote:
                if source[i] == "\\":
                    out[i] = " "
                    i += 1
                    if i < n:
                        out[i] = " "
                        i += 1
                    continue
                out[i] = " "
                i += 1
            if i < n:
                i += 1
        elif ch == "/" and i + 1 < n and source[i + 1] == "/":
            # A //@version pragma is meaningful; keep it, blank other comments.
            eol = source.find("\n", i)
            eol = n if eol == -1 else eol
            if not re.match(r"//\s*@version", source[i:eol]):
                for j in range(i, eol):
                    out[j] = " "
            i = eol
        else:
            i += 1
    return "".join(out)


def check_version(source: str, report: Report) -> int | None:
    versions = VERSION_RE.findall(source)
    if not versions:
        report.add("ERROR", 1, "missing //@version= pragma", "Add `//@version=6` as the very first line. Without it TradingView compiles the script as v1.")
        return None
    if len(versions) > 1:
        lines = [source[: m.start()].count("\n") + 1 for m in VERSION_RE.finditer(source)]
        report.add("ERROR", lines[1], f"{len(versions)} //@version pragmas found (lines {lines})", "One script = one file. Two version headers is a compile error — split into separate files.")
    version = int(versions[0])
    if version < 5:
        report.add("WARNING", 1, f"//@version={version} is legacy", "v5 and v6 are the supported versions; v4 and below use bare function names (rsi() not ta.rsi()).")
    return version


def check_declarations(source: str, report: Report) -> None:
    matches = list(DECLARATION_RE.finditer(source))
    if not matches:
        report.add("ERROR", 1, "no strategy(), indicator(), or library() declaration", "Every Pine script needs exactly one declaration statement after the version pragma.")
        return
    if len(matches) > 1:
        lines = [source[: m.start()].count("\n") + 1 for m in matches]
        kinds = [m.group(1) for m in matches]
        report.add("ERROR", lines[1], f"{len(matches)} script declarations found: {', '.join(kinds)} at lines {lines}", "One script = one file. Two declarations in one file is a compile error — this usually means two scripts were concatenated.")


def check_identifiers(source: str, report: Report) -> None:
    for match in NAMESPACED_RE.finditer(source):
        namespace, member = match.group(1), match.group(2)
        full = f"{namespace}.{member}"
        line = source[: match.start()].count("\n") + 1

        if full in KNOWN_INVALID:
            report.add("ERROR", line, f"`{full}` does not exist", KNOWN_INVALID[full])
            continue

        if namespace in UNPOLICED or namespace not in NAMESPACES:
            continue

        if member not in NAMESPACES[namespace]:
            report.add(
                "WARNING",
                line,
                f"`{full}` is not in this validator's built-in list",
                f"Verify it against the Pine reference. The `{namespace}.` inventory here is hand-maintained and incomplete, so this may well be valid.",
            )


def check_brackets(source: str, report: Report) -> None:
    pairs = {")": "(", "]": "[", "}": "{"}
    stack: list[tuple[str, int]] = []
    line = 1
    for ch in source:
        if ch == "\n":
            line += 1
        elif ch in "([{":
            stack.append((ch, line))
        elif ch in ")]}":
            if not stack:
                report.add("ERROR", line, f"unmatched closing `{ch}`")
                return
            opener, opened_at = stack.pop()
            if opener != pairs[ch]:
                report.add("ERROR", line, f"mismatched bracket: `{opener}` opened at line {opened_at}, closed with `{ch}`")
                return
    for opener, opened_at in stack:
        report.add("ERROR", opened_at, f"unclosed `{opener}`")


def check_v6_migration(source: str, version: int | None, report: Report) -> None:
    if version is None:
        return

    for name, advice in V6_DEPRECATED.items():
        for match in re.finditer(rf"(?<![.\w]){re.escape(name)}\s*\(", source):
            line = source[: match.start()].count("\n") + 1
            if name == "alertcondition":
                # Valid in v5 and v6 — superseded, not removed. Saying
                # "not valid" here would be plainly wrong.
                report.add("WARNING", line, f"`alertcondition(...)` is supported in v{version} but superseded", advice)
                continue
            if version < 5:
                continue
            report.add("ERROR", line, f"`{name}(...)` is not valid in v{version}", advice)

    if version >= 6:
        for i, raw in enumerate(source.splitlines(), 1):
            match = VAR_DECL_RE.match(raw)
            if match and match.group(1) is None:
                report.add("WARNING", i, f"`var {match.group(2)}` has no explicit type", "v6 tightened type inference on var declarations — prefer `var float x = 0.0`.")


def check_backtest_realism(source: str, report: Report) -> None:
    """Flag strategy settings that make a backtest unreproducible live.

    Only runs on strategy() scripts. These are warnings, not errors — they
    are modelling choices, not syntax. But they are the choices that most
    often separate a profitable-looking equity curve from a tradeable one.
    """
    match = re.search(r"\bstrategy\s*\(", source)
    if not match:
        return

    # Slice out the declaration's argument list so a `commission_value` used
    # elsewhere in the script isn't mistaken for a declaration argument.
    start = match.end() - 1
    depth = 0
    end = start
    for i in range(start, len(source)):
        if source[i] == "(":
            depth += 1
        elif source[i] == ")":
            depth -= 1
            if depth == 0:
                end = i
                break
    decl = source[start : end + 1]
    line = source[:start].count("\n") + 1

    if "commission_value" not in decl:
        report.add("WARNING", line, "strategy() sets no commission", "Add commission_type + commission_value. On an intraday scalper, costs routinely flip a profitable backtest negative.")
    if "slippage" not in decl:
        report.add("WARNING", line, "strategy() sets no slippage", "Add `slippage = <ticks>`. It is measured in ticks, not currency.")

    if "process_orders_on_close" in decl and re.search(r"process_orders_on_close\s*=\s*true", decl):
        report.add(
            "WARNING",
            line,
            "process_orders_on_close = true is optimistic",
            "It fills at the signal bar's close, assuming you saw that close and traded at it simultaneously. Set it false unless you are modelling on-close execution deliberately.",
        )

    if "percent_of_equity" in decl and "cash_per_contract" in decl:
        report.add(
            "WARNING",
            line,
            "percent_of_equity sizing combined with cash_per_contract commission",
            "Position size then varies with equity while commission is charged per contract, so modelled costs drift from reality as the curve compounds. Use strategy.fixed sizing, or a percent commission.",
        )

    if re.search(r"default_qty_type\s*=\s*strategy\.percent_of_equity", decl) and re.search(r"default_qty_value\s*=\s*100\b", decl):
        report.add(
            "WARNING",
            line,
            "sizing is 100% of equity per trade",
            "Every trade risks the full account and compounds. Size from stop distance instead so risk per trade is constant.",
        )


def check_shadowing(source: str, report: Report) -> None:
    """Repeated declaration of the same name — Pine allows it, silently.

    A real footgun when two scripts are merged: the second declaration wins
    and the first block quietly reads a value it never computed.
    """
    seen: dict[str, int] = {}
    for i, raw in enumerate(source.splitlines(), 1):
        match = VAR_DECL_RE.match(raw)
        if not match:
            continue
        name = match.group(2)
        if name in seen:
            report.add("WARNING", i, f"`{name}` re-declared with var (first at line {seen[name]})", "Pine permits this but the later declaration wins; merged scripts sharing a name is a classic source of silently-wrong values.")
        else:
            seen[name] = i


def validate(source: str, path: str) -> Report:
    report = Report(path=path)
    clean = _strip_noise(source)

    version = check_version(clean, report)
    check_declarations(clean, report)
    check_brackets(clean, report)
    check_identifiers(clean, report)
    check_v6_migration(clean, version, report)
    check_backtest_realism(clean, report)
    check_shadowing(clean, report)

    report.findings.sort(key=lambda f: (SEVERITY_ORDER[f.severity], f.line))
    return report


def render(report: Report, quiet: bool = False) -> str:
    lines = [f"{report.path}"]
    if not report.findings:
        lines.append("  OK — no issues found")
        return "\n".join(lines)

    for finding in report.findings:
        if quiet and finding.severity != "ERROR":
            continue
        lines.append(f"  {finding.severity:<7} line {finding.line}: {finding.message}")
        if finding.hint:
            lines.append(f"          → {finding.hint}")

    lines.append(f"  {report.errors} error(s), {report.warnings} warning(s)")
    if report.warnings and not report.errors:
        lines.append("  Warnings are advisory — the built-in inventory is incomplete by design.")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Validate TradingView Pine Script v5/v6.")
    parser.add_argument("files", nargs="*", help="Pine files to check ('-' for stdin)")
    parser.add_argument("--quiet", action="store_true", help="Show errors only")
    parser.add_argument("--list-builtins", action="store_true", help="Print the known built-in inventory and exit")
    args = parser.parse_args(argv)

    if args.list_builtins:
        for namespace in sorted(NAMESPACES):
            print(f"{namespace}. ({len(NAMESPACES[namespace])} known)")
            print("  " + ", ".join(sorted(NAMESPACES[namespace])))
        print("\nKnown-invalid identifiers (reported as errors):")
        for name, why in sorted(KNOWN_INVALID.items()):
            print(f"  {name}\n    → {why}")
        return 0

    if not args.files:
        parser.print_usage()
        return 2

    total_errors = 0
    for target in args.files:
        if target == "-":
            source, label = sys.stdin.read(), "<stdin>"
        else:
            path = Path(target)
            if not path.is_file():
                print(f"{target}\n  ERROR   file not found")
                total_errors += 1
                continue
            source, label = path.read_text(encoding="utf-8"), str(path)

        report = validate(source, label)
        print(render(report, quiet=args.quiet))
        total_errors += report.errors

    return 1 if total_errors else 0


if __name__ == "__main__":
    sys.exit(main())
