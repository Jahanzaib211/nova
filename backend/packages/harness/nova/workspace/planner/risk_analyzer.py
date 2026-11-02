"""Risk Analyzer — evaluates risk level of execution steps.

Phase C9 — each step in an execution plan is analyzed for risk
before execution.  Risk levels map to execution policies:
- LOW: execute without guard
- MEDIUM: log and confirm
- HIGH: require explicit approval
- CRITICAL: block and escalate

2026-07 audit (B5): detection is tokenized, not substring-based.
``sudo``/``env`` prefixes are unwrapped and ``sh -c``-style payloads are
shlex-split and re-analyzed, so flag reordering ("rm -fr", "git clean -d -f")
and shell-wrapped commands cannot bypass the gate, while quoting dangerous
text as data (``echo "rm -rf /"``) is not flagged.
"""

from __future__ import annotations

import shlex
from collections.abc import Iterator
from dataclasses import dataclass

from deerflow.workspace.models.execution_plan import ExecutionPlan, ExecutionStep, RiskLevel, StepKind

_SHELL_PROGRAMS = {"bash", "sh", "zsh", "dash", "ksh"}
_WRAPPER_PROGRAMS = {"sudo", "env", "nice", "nohup", "time", "timeout"}
_ROOT_TARGETS = {"/", "/*", "~", "$HOME", "/home", "/etc", "/usr", "/var", "/boot"}
_FORK_BOMB = ":(){:|:&};:"


def _program_name(token: str) -> str:
    return token.rsplit("/", 1)[-1]


def _split_flags(argv: list[str]) -> tuple[set[str], list[str]]:
    """Return (single-char flags seen, positional args) for a simple command."""
    flags: set[str] = set()
    positional: list[str] = []
    for token in argv:
        if token.startswith("--"):
            flags.add(token[2:])
        elif token.startswith("-") and len(token) > 1:
            flags.update(token[1:])
        else:
            positional.append(token)
    return flags, positional


def _iter_commands(argv: tuple[str, ...] | list[str], depth: int = 0) -> Iterator[list[str]]:
    """Yield every concrete command reachable from ``argv``.

    Unwraps wrapper programs (sudo, env, ...) and recurses into shell ``-c``
    payloads (splitting on ``&&``, ``;``, ``|`` connectors after shlex).
    """
    tokens = [str(t) for t in argv if str(t)]
    if not tokens or depth > 4:
        return

    program = _program_name(tokens[0])

    if program in _WRAPPER_PROGRAMS:
        rest = tokens[1:]
        # skip wrapper options and env-style VAR=VALUE assignments
        while rest and (rest[0].startswith("-") or ("=" in rest[0] and not rest[0].startswith("/"))):
            rest = rest[1:]
        yield from _iter_commands(rest, depth + 1)
        return

    if program in _SHELL_PROGRAMS and "-c" in tokens[:3]:
        c_index = tokens.index("-c")
        if c_index + 1 < len(tokens):
            payload = tokens[c_index + 1]
            try:
                payload_tokens = shlex.split(payload)
            except ValueError:
                payload_tokens = payload.split()
            # split on shell connectors so each sub-command is analyzed
            current: list[str] = []
            for token in payload_tokens:
                if token in ("&&", "||", ";", "|", "&"):
                    if current:
                        yield from _iter_commands(current, depth + 1)
                    current = []
                else:
                    current.append(token)
            if current:
                yield from _iter_commands(current, depth + 1)
        return

    yield tokens


@dataclass
class RiskAnalyzer:
    """Analyze execution steps for risk level.

    Considers:
    - Step kind (delete > run > read)
    - Destructive / system-wide flags on the step
    - Tokenized command analysis (sudo unwrapping, shell -c recursion)
    """

    def _raise(self, current: RiskLevel, target: RiskLevel) -> RiskLevel:
        return target if target.order > current.order else current

    def analyze_step(self, step: ExecutionStep) -> RiskLevel:
        """Return the risk level for a single step."""
        risk = RiskLevel.LOW

        if step.kind == StepKind.RUN_COMMAND:
            risk = self._raise(risk, RiskLevel.MEDIUM)
        if step.kind == StepKind.DELETE:
            risk = self._raise(risk, RiskLevel.HIGH)

        if step.is_destructive:
            risk = self._raise(risk, RiskLevel.HIGH)

        if step.is_system_wide:
            risk = self._raise(risk, RiskLevel.HIGH)

        risk = self._raise(risk, self._analyze_argv(step.argv))
        return risk

    def analyze_plan(self, plan: ExecutionPlan) -> RiskLevel:
        """Return the maximum risk level across all steps."""
        if not plan.steps:
            return RiskLevel.LOW
        risks = [self.analyze_step(s) for s in plan.steps]
        return max(risks, key=lambda r: r.order)

    def approve_plan(self, plan: ExecutionPlan) -> bool:
        """Return True if the plan is auto-approvable (at most MEDIUM risk).

        Compares by ``.order`` — ``str, Enum`` members compare alphabetically,
        which silently approved CRITICAL plans ("critical" <= "medium").
        """
        return self.analyze_plan(plan).order <= RiskLevel.MEDIUM.order

    # -- tokenized command analysis --------------------------------------

    def _analyze_argv(self, argv: tuple[str, ...]) -> RiskLevel:
        if not argv:
            return RiskLevel.LOW

        risk = RiskLevel.LOW
        raw = " ".join(str(a) for a in argv).replace(" ", "")
        if _FORK_BOMB in raw:
            return RiskLevel.CRITICAL

        outer_program = _program_name(str(argv[0]))
        if outer_program == "sudo":
            risk = self._raise(risk, RiskLevel.HIGH)

        for command in _iter_commands(argv):
            risk = self._raise(risk, self._analyze_command(command))
            if risk == RiskLevel.CRITICAL:
                break
        return risk

    def _analyze_command(self, command: list[str]) -> RiskLevel:
        if not command:
            return RiskLevel.LOW
        program = _program_name(command[0])
        flags, positional = _split_flags(command[1:])

        if program.startswith("mkfs"):
            return RiskLevel.CRITICAL

        if program == "dd":
            for token in command[1:]:
                if token.startswith("of=/dev/"):
                    return RiskLevel.CRITICAL
            return RiskLevel.MEDIUM

        if program == "rm":
            recursive = bool({"r", "R"} & flags) or "recursive" in flags
            force = "f" in flags or "force" in flags
            if recursive and force:
                if any(p in _ROOT_TARGETS or p.rstrip("/") in ("", "~") for p in positional):
                    return RiskLevel.CRITICAL
                return RiskLevel.HIGH
            if recursive or force:
                return RiskLevel.MEDIUM
            return RiskLevel.LOW

        if program == "chmod" and ({"R"} & flags) and any(p in _ROOT_TARGETS for p in positional):
            return RiskLevel.CRITICAL

        if program == "git":
            return self._analyze_git(flags, positional)

        return RiskLevel.LOW

    def _analyze_git(self, flags: set[str], positional: list[str]) -> RiskLevel:
        subcommand = positional[0] if positional else ""
        if subcommand == "reset" and "hard" in flags:
            return RiskLevel.HIGH
        if subcommand == "clean" and ("f" in flags or "force" in flags):
            return RiskLevel.HIGH
        if subcommand == "push" and ("f" in flags or "force" in flags or "force-with-lease" in flags):
            return RiskLevel.HIGH
        if subcommand == "checkout" and "f" in flags:
            return RiskLevel.MEDIUM
        return RiskLevel.LOW
