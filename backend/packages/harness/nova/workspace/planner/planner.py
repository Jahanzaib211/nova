"""Workspace Planner — builds execution plans from workspace queries.

Phase C9 — the planner is the core API for the WIK.  Given a natural
language intent or structured query, it:
1. Resolves the intent against the workspace graph
2. Selects relevant projects and files
3. Builds an ordered ExecutionPlan with steps
4. Validates the plan
5. Returns the plan for kernel execution
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from deerflow.workspace.graph.command_registry import CommandRegistry
from deerflow.workspace.graph.dependency_graph import DependencyGraph
from deerflow.workspace.graph.symbol_index import SymbolIndex
from deerflow.workspace.graph.workspace_graph import WorkspaceGraph
from deerflow.workspace.models.execution_plan import ExecutionPlan, ExecutionStep, RiskLevel, StepKind
from deerflow.workspace.planner.plan_validator import PlanValidationResult, PlanValidator
from deerflow.workspace.planner.risk_analyzer import RiskAnalyzer


@dataclass
class PlannerConfig:
    max_steps: int = 20
    max_depth: int = 6
    auto_approve: bool = True


@dataclass
class PlannerResult:
    plan: ExecutionPlan | None
    validation: PlanValidationResult
    symbols_found: list[str]
    projects_found: list[str]


@dataclass
class WorkspacePlanner:
    """Build ExecutionPlans from workspace queries.

    The planner uses the workspace graph, symbol index, dependency
    graph, and command registry to build deterministic plans for:
    - Grep/search queries
    - File modifications
    - Build/test/run commands
    - Refactoring across files
    """

    graph: WorkspaceGraph
    symbols: SymbolIndex
    deps: DependencyGraph
    commands: CommandRegistry
    config: PlannerConfig = field(default_factory=PlannerConfig)

    _validator: PlanValidator = field(default_factory=PlanValidator)
    _risk_analyzer: RiskAnalyzer = field(default_factory=RiskAnalyzer)

    def plan_search(
        self,
        query: str,
        project_id: str | None = None,
        file_pattern: str | None = None,
    ) -> PlannerResult:
        """Plan a search query across the workspace.

        Returns an ExecutionPlan with read/search steps bounded
        by TraversalLimit (max_depth=6, max_files=50k, max_time=15s).
        """
        projects = [p for p in self.graph.find_projects() if p.project_id == (project_id or p.project_id)]

        symbols_found = []
        for proj in projects:
            syms = self.symbols.find_prefix(query)
            symbols_found.extend(s.fqn for s in syms)

        step = ExecutionStep(
            step_id="search-0",
            kind=StepKind.RUN_COMMAND,
            description=f"Search workspace for '{query}'",
            argv=("grep_files", query, file_pattern or "*"),
            risk_level=RiskLevel.LOW,
        )

        plan = ExecutionPlan(
            plan_id="search",
            goal=f"Search: {query}",
            steps=(step,),
        )

        validation = self._validator.validate(plan)
        return PlannerResult(plan=plan, validation=validation, symbols_found=symbols_found, projects_found=[p.project_id for p in projects])

    def plan_read_file(self, file_path: str) -> PlannerResult:
        """Plan reading a specific file."""
        step = ExecutionStep(
            step_id=f"read-{hash(file_path) % 10000:04d}",
            kind=StepKind.READ,
            description=f"Read {file_path}",
            argv=("read_file", file_path),
            risk_level=RiskLevel.LOW,
        )

        plan = ExecutionPlan(
            plan_id=f"read-{Path(file_path).name}",
            goal=f"Read: {Path(file_path).name}",
            steps=(step,),
        )

        validation = self._validator.validate(plan)
        return PlannerResult(plan=plan, validation=validation, symbols_found=[], projects_found=[])

    def plan_edit_file(
        self,
        file_path: str,
        old_string: str,
        new_string: str,
    ) -> PlannerResult:
        """Plan editing a specific file with old→new replacement."""
        affected = self.deps.affected_by(file_path)

        step = ExecutionStep(
            step_id=f"edit-{hash(file_path) % 10000:04d}",
            kind=StepKind.EDIT,
            description=f"Edit {file_path}",
            argv=("edit_file", file_path, old_string, new_string),
            risk_level=RiskLevel.MEDIUM,
        )

        extra_steps = []
        for i, dep_path in enumerate(sorted(affected)[:5], 1):
            extra_steps.append(
                ExecutionStep(
                    step_id=f"read-dep-{i}",
                    kind=StepKind.READ,
                    description=f"Read dependent file: {dep_path}",
                    argv=("read_file", dep_path),
                    risk_level=RiskLevel.LOW,
                )
            )

        plan = ExecutionPlan(
            plan_id=f"edit-{Path(file_path).name}",
            goal=f"Edit: {Path(file_path).name}",
            steps=tuple([step] + extra_steps),
        )

        validation = self._validator.validate(plan)
        return PlannerResult(plan=plan, validation=validation, symbols_found=[], projects_found=[])

    def plan_run_command(
        self,
        command_name: str,
        project_id: str | None = None,
    ) -> PlannerResult:
        """Plan running a named command (e.g. 'test', 'build')."""
        cmds = self.commands.find_by_name(command_name)
        if project_id:
            cmds = [c for c in cmds if c.project_id == project_id]

        if not cmds:
            return PlannerResult(
                plan=None,
                validation=PlanValidationResult(is_valid=False, errors=[f"Command '{command_name}' not found"], warnings=[]),
                symbols_found=[],
                projects_found=[],
            )

        steps = []
        for i, cmd in enumerate(cmds[: self.config.max_steps]):
            steps.append(
                ExecutionStep(
                    step_id=f"cmd-{i}",
                    kind=StepKind.RUN_COMMAND,
                    description=cmd.description or f"Run {cmd.name}",
                    argv=cmd.argv,
                    risk_level=RiskLevel.HIGH if cmd.is_destructive else RiskLevel.MEDIUM,
                    cwd=cmd.cwd,
                )
            )

        plan = ExecutionPlan(
            plan_id=f"run-{command_name}",
            goal=f"Run: {command_name}",
            steps=tuple(steps),
        )

        validation = self._validator.validate(plan)
        return PlannerResult(
            plan=plan,
            validation=validation,
            symbols_found=[],
            projects_found=[cmd.project_id for cmd in cmds],
        )

    def validate(self, plan: ExecutionPlan) -> PlanValidationResult:
        """Validate a plan without executing it."""
        return self._validator.validate(plan)

    def risk_of(self, plan: ExecutionPlan) -> RiskLevel:
        """Return the risk level of a plan."""
        return self._risk_analyzer.analyze_plan(plan)
