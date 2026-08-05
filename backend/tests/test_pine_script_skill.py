"""Tests for the bundled pine-script skill and its validator.

The validator's contract matters more than its coverage: it must never report
an ERROR for something merely unrecognised, because a validator that rejects
valid code trains you to ignore it. Warnings are the escape hatch; errors are
reserved for things proven wrong.
"""

from __future__ import annotations

import importlib.util
import subprocess
import sys
from pathlib import Path

import pytest

SKILL_DIR = Path(__file__).resolve().parents[2] / "skills" / "public" / "pine-script"
VALIDATOR = SKILL_DIR / "scripts" / "validate_pine.py"


@pytest.fixture(scope="module")
def validator():
    spec = importlib.util.spec_from_file_location("validate_pine", VALIDATOR)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    # @dataclass resolves its module globals through sys.modules, so the
    # module has to be registered before exec_module or it raises on the
    # first dataclass definition.
    sys.modules["validate_pine"] = module
    try:
        spec.loader.exec_module(module)
        yield module
    finally:
        sys.modules.pop("validate_pine", None)


def _check(validator, source: str):
    return validator.validate(source, "<test>")


def _severities(report) -> dict[str, list[str]]:
    out: dict[str, list[str]] = {"ERROR": [], "WARNING": []}
    for finding in report.findings:
        out.setdefault(finding.severity, []).append(finding.message)
    return out


class TestSkillLayout:
    def test_skill_md_exists_with_required_frontmatter(self) -> None:
        text = (SKILL_DIR / "SKILL.md").read_text(encoding="utf-8")
        assert text.startswith("---")
        assert "name: pine-script" in text
        assert "description:" in text

    def test_frontmatter_keys_are_all_allowed(self) -> None:
        """An unknown frontmatter key fails skill validation at load time."""
        from deerflow.skills.validation import ALLOWED_FRONTMATTER_PROPERTIES

        text = (SKILL_DIR / "SKILL.md").read_text(encoding="utf-8")
        block = text.split("---", 2)[1]
        keys = {line.split(":", 1)[0].strip() for line in block.splitlines() if line.strip() and not line.startswith((" ", "\t", "#"))}
        assert keys <= set(ALLOWED_FRONTMATTER_PROPERTIES), f"disallowed frontmatter keys: {keys - set(ALLOWED_FRONTMATTER_PROPERTIES)}"

    def test_parses_as_a_real_skill(self) -> None:
        from deerflow.skills.parser import parse_skill_file
        from deerflow.skills.types import SkillCategory

        skill = parse_skill_file(SKILL_DIR / "SKILL.md", SkillCategory.PUBLIC)
        assert skill is not None, "SKILL.md failed to parse — the skill would be invisible to the agent"
        assert skill.name == "pine-script"
        assert "pine" in skill.description.lower()

    @pytest.mark.parametrize(
        "relative",
        [
            "scripts/validate_pine.py",
            "references/v5-to-v6.md",
            "references/common-errors.md",
            "references/tradingview-automation.md",
            "templates/indicator.pine",
            "templates/strategy.pine",
        ],
    )
    def test_bundled_assets_exist(self, relative: str) -> None:
        assert (SKILL_DIR / relative).is_file()

    def test_skill_md_references_only_files_that_exist(self) -> None:
        """A skill that points at a missing reference wastes a whole turn."""
        text = (SKILL_DIR / "SKILL.md").read_text(encoding="utf-8")
        for token in ("references/v5-to-v6.md", "references/common-errors.md", "references/tradingview-automation.md", "scripts/validate_pine.py"):
            if token in text:
                assert (SKILL_DIR / token).is_file(), f"SKILL.md references missing {token}"


class TestShippedTemplates:
    @pytest.mark.parametrize("name", ["indicator.pine", "strategy.pine"])
    def test_templates_validate_clean(self, validator, name: str) -> None:
        report = _check(validator, (SKILL_DIR / "templates" / name).read_text(encoding="utf-8"))
        assert report.errors == 0, _severities(report)["ERROR"]
        assert report.warnings == 0, _severities(report)["WARNING"]

    def test_strategy_template_sets_realistic_costs(self, validator) -> None:
        """Omitting commission/slippage is the top reason a backtest looks
        profitable and live trading isn't."""
        text = (SKILL_DIR / "templates" / "strategy.pine").read_text(encoding="utf-8")
        for required in ("commission_type", "commission_value", "slippage", "pyramiding"):
            assert required in text

    def test_strategy_template_pins_lookahead_off(self, validator) -> None:
        text = (SKILL_DIR / "templates" / "strategy.pine").read_text(encoding="utf-8")
        assert "barmerge.lookahead_off" in text


class TestKnownInvalidIdentifiers:
    @pytest.mark.parametrize(
        "identifier",
        ["strategy.profit_factor", "strategy.avg_winning_trade", "strategy.avg_losing_trade", "ta.crossabove", "ta.stddev"],
    )
    def test_reported_as_errors_with_a_fix(self, validator, identifier: str) -> None:
        """These caused a real 'Undeclared identifier' compile error in a
        prior session — the whole reason this validator exists."""
        report = _check(validator, f'//@version=6\nstrategy("T")\nx = {identifier}\n')
        errors = _severities(report)["ERROR"]
        assert any(identifier in e for e in errors), errors
        hints = [f.hint for f in report.findings if f.severity == "ERROR"]
        assert any(hints), "an error about a non-existent identifier must suggest the replacement"

    @pytest.mark.parametrize(
        "identifier",
        [
            "strategy.max_drawdown",
            "strategy.grossprofit",
            "strategy.grossloss",
            "strategy.netprofit",
            "strategy.wintrades",
            "strategy.losstrades",
            "strategy.closedtrades",
            "strategy.equity",
            "strategy.position_size",
        ],
    )
    def test_real_builtins_are_not_flagged(self, validator, identifier: str) -> None:
        """strategy.max_drawdown in particular was once mis-recorded as
        invalid alongside the UI-only metrics. It is real."""
        report = _check(validator, f'//@version=6\nstrategy("T")\nx = {identifier}\n')
        assert report.errors == 0, _severities(report)["ERROR"]
        # Scoped to identifier findings: this bare declaration also trips the
        # backtest-realism warnings, which are a separate concern.
        assert not [f for f in report.findings if identifier in f.message], _severities(report)["WARNING"]


class TestStructuralChecks:
    def test_missing_version_pragma_is_an_error(self, validator) -> None:
        report = _check(validator, 'strategy("T")\nplot(close)\n')
        assert any("@version" in m for m in _severities(report)["ERROR"])

    def test_two_version_pragmas_is_an_error(self, validator) -> None:
        report = _check(validator, '//@version=6\nstrategy("A")\n//@version=6\nstrategy("B")\n')
        assert any("version" in m for m in _severities(report)["ERROR"])

    def test_two_declarations_is_an_error(self, validator) -> None:
        """One script = one file; two strategy() calls is a compile error."""
        report = _check(validator, '//@version=6\nstrategy("A")\nplot(close)\nstrategy("B")\n')
        assert any("declaration" in m for m in _severities(report)["ERROR"])

    def test_missing_declaration_is_an_error(self, validator) -> None:
        report = _check(validator, "//@version=6\nplot(close)\n")
        assert any("declaration" in m for m in _severities(report)["ERROR"])

    def test_unbalanced_bracket_is_an_error(self, validator) -> None:
        report = _check(validator, '//@version=6\nstrategy("T")\nx = ta.ema(close, 9\nplot(x)\n')
        assert any("unclosed" in m or "unmatched" in m for m in _severities(report)["ERROR"])

    def test_v4_bare_function_is_an_error_in_v6(self, validator) -> None:
        report = _check(validator, '//@version=6\nstrategy("T")\nx = rsi(close, 14)\n')
        assert any("rsi" in m for m in _severities(report)["ERROR"])


class TestBacktestRealism:
    """These are modelling defects, not syntax — warnings, never errors.

    They matter because they are what separates an equity curve that looks
    profitable from one that could have been traded. A backtest with
    100%-of-equity sizing and per-contract commission produces numbers that
    do not mean anything.
    """

    def _decl(self, extra: str) -> str:
        return f'//@version=6\nstrategy("T", overlay=true{extra})\nplot(close)\n'

    def test_missing_commission_warns(self, validator) -> None:
        report = _check(validator, self._decl(", slippage=2"))
        assert report.errors == 0
        assert any("commission" in m for m in _severities(report)["WARNING"])

    def test_missing_slippage_warns(self, validator) -> None:
        report = _check(validator, self._decl(", commission_type=strategy.commission.cash_per_contract, commission_value=0.5"))
        assert any("slippage" in m for m in _severities(report)["WARNING"])

    def test_process_orders_on_close_true_warns(self, validator) -> None:
        report = _check(validator, self._decl(", commission_value=0.5, slippage=2, process_orders_on_close=true"))
        assert any("optimistic" in m for m in _severities(report)["WARNING"])

    def test_process_orders_on_close_false_is_silent(self, validator) -> None:
        report = _check(validator, self._decl(", commission_value=0.5, slippage=2, process_orders_on_close=false"))
        assert not any("optimistic" in m for m in _severities(report)["WARNING"])

    def test_percent_equity_with_per_contract_commission_warns(self, validator) -> None:
        report = _check(
            validator,
            self._decl(", default_qty_type=strategy.percent_of_equity, default_qty_value=100, commission_type=strategy.commission.cash_per_contract, commission_value=0.5, slippage=2"),
        )
        warnings = _severities(report)["WARNING"]
        assert any("percent_of_equity" in m for m in warnings)
        assert any("100% of equity" in m for m in warnings)

    def test_fixed_sizing_with_costs_is_clean(self, validator) -> None:
        report = _check(
            validator,
            self._decl(", default_qty_type=strategy.fixed, default_qty_value=1, commission_type=strategy.commission.cash_per_contract, commission_value=0.5, slippage=2, process_orders_on_close=false"),
        )
        assert report.errors == 0
        assert report.warnings == 0

    def test_indicator_scripts_are_not_checked_for_costs(self, validator) -> None:
        """An indicator has no orders to model; warning about commission
        would be pure noise."""
        report = _check(validator, '//@version=6\nindicator("T", overlay=true)\nplot(close)\n')
        assert report.warnings == 0

    def test_commission_used_outside_the_declaration_is_not_credited(self, validator) -> None:
        """Only the strategy() argument list counts — a variable named
        commission_value elsewhere must not suppress the warning."""
        source = '//@version=6\nstrategy("T", overlay=true, slippage=2)\ncommission_value = 0.5\nplot(close)\n'
        report = _check(validator, source)
        assert any("commission" in m for m in _severities(report)["WARNING"])


class TestNoFalsePositives:
    def test_unknown_identifier_warns_but_never_errors(self, validator) -> None:
        """The core contract. The built-in inventory is incomplete, so an
        unrecognised name must never fail the run."""
        report = _check(validator, '//@version=6\nstrategy("T")\nx = ta.some_new_v7_function(close)\n')
        assert report.errors == 0
        assert report.warnings >= 1

    def test_unpoliced_namespaces_are_silent(self, validator) -> None:
        source = '//@version=6\nindicator("T")\nlabel.new(bar_index, high, "x", style=label.style_label_down, color=color.new(color.red, 20))\n'
        report = _check(validator, source)
        assert report.errors == 0
        assert report.warnings == 0

    def test_identifiers_inside_strings_and_comments_are_ignored(self, validator) -> None:
        """Blanking string and comment content is what keeps documentation
        mentioning strategy.profit_factor from failing its own file."""
        source = '//@version=6\nstrategy("T")\n// avoid strategy.profit_factor here\nmsg = "use ta.crossabove instead"\nplot(close)\n'
        report = _check(validator, source)
        assert report.errors == 0

    def test_alertcondition_warns_and_does_not_error(self, validator) -> None:
        """Superseded by alert(), but valid in both v5 and v6 — calling it
        invalid would be plainly wrong."""
        report = _check(validator, '//@version=5\nindicator("T")\nalertcondition(close > open, "up", "up")\n')
        assert report.errors == 0
        assert any("superseded" in f.hint or "superseded" in f.message for f in report.findings)


class TestCli:
    def test_clean_file_exits_zero(self, tmp_path: Path) -> None:
        target = tmp_path / "ok.pine"
        target.write_text('//@version=6\nindicator("T")\nplot(ta.ema(close, 9))\n')
        result = subprocess.run([sys.executable, str(VALIDATOR), str(target)], capture_output=True, text=True, check=False)
        assert result.returncode == 0
        assert "OK" in result.stdout

    def test_broken_file_exits_one(self, tmp_path: Path) -> None:
        target = tmp_path / "bad.pine"
        target.write_text('//@version=6\nstrategy("T")\nx = strategy.profit_factor\n')
        result = subprocess.run([sys.executable, str(VALIDATOR), str(target)], capture_output=True, text=True, check=False)
        assert result.returncode == 1
        assert "does not exist" in result.stdout

    def test_list_builtins_works(self) -> None:
        result = subprocess.run([sys.executable, str(VALIDATOR), "--list-builtins"], capture_output=True, text=True, check=False)
        assert result.returncode == 0
        assert "ta." in result.stdout
        assert "strategy.profit_factor" in result.stdout

    def test_stdin_is_accepted(self) -> None:
        result = subprocess.run(
            [sys.executable, str(VALIDATOR), "-"],
            input='//@version=6\nindicator("T")\nplot(close)\n',
            capture_output=True,
            text=True,
            check=False,
        )
        assert result.returncode == 0

    def test_missing_file_is_reported_not_crashed(self, tmp_path: Path) -> None:
        result = subprocess.run([sys.executable, str(VALIDATOR), str(tmp_path / "nope.pine")], capture_output=True, text=True, check=False)
        assert result.returncode == 1
        assert "not found" in result.stdout
