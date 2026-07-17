"""Plan Validator — validates execution plans before execution.

Phase C9 — validates that a plan is:
- Non-empty
- Within resource budgets
- Has no cycles
- Has valid step dependencies
- Has approved risk level

2026-07 audit (C5): dependency validation is real — unknown references,
self-dependencies, cycles, and over-deep chains are errors; plans whose
analyzed risk requires explicit approval carry a warning.
"""

from __future__ import annotations

from dataclasses import dataclass

from deerflow.workspace.models.execution_plan import ExecutionPlan, RiskLevel


@dataclass
class PlanValidationResult:
    is_valid: bool
    errors: list[str]
    warnings: list[str]


@dataclass
class PlanValidator:
    """Validate an ExecutionPlan before it is handed to the kernel.

    Validation rules:
    - Plan must have at least one step
    - Total steps must not exceed 50
    - Steps with dependencies must reference valid step IDs
    - No step may depend on itself
    - Dependency graph must be acyclic
    - Dependency chains must not exceed MAX_DEPTH
    - Plans above MEDIUM analyzed risk carry a warning (execution requires
      explicit approval; see WorkspaceIntelligenceServiceImpl.execute_plan)
    """

    MAX_STEPS = 50
    MAX_DEPTH = 10

    def validate(self, plan: ExecutionPlan) -> PlanValidationResult:
        errors: list[str] = []
        warnings: list[str] = []

        if not plan.steps:
            errors.append("Plan is empty")
            return PlanValidationResult(is_valid=False, errors=errors, warnings=warnings)

        if len(plan.steps) > self.MAX_STEPS:
            errors.append(f"Plan has {len(plan.steps)} steps, maximum is {self.MAX_STEPS}")

        step_ids = {s.step_id for s in plan.steps}
        if len(step_ids) != len(plan.steps):
            errors.append("Duplicate step IDs found in plan")

        errors.extend(self._validate_dependencies(plan, step_ids))
        warnings.extend(self._risk_warnings(plan))

        return PlanValidationResult(
            is_valid=len(errors) == 0,
            errors=errors,
            warnings=warnings,
        )

    def _validate_dependencies(self, plan: ExecutionPlan, step_ids: set[str]) -> list[str]:
        errors: list[str] = []
        deps: dict[str, tuple[str, ...]] = {}

        for step in plan.steps:
            deps[step.step_id] = step.depends_on
            for dep in step.depends_on:
                if dep == step.step_id:
                    errors.append(f"Step '{step.step_id}' depends on itself")
                elif dep not in step_ids:
                    errors.append(f"Step '{step.step_id}' depends on unknown step '{dep}'")

        if errors:
            return errors

        # Depth-first walk: detects cycles and measures chain depth in one pass.
        # depth[s] = 1 + max(depth of dependencies); UNRESOLVED marks the stack.
        UNRESOLVED = -1
        depth: dict[str, int] = {}

        def resolve(step_id: str, chain: list[str]) -> int:
            known = depth.get(step_id)
            if known == UNRESOLVED:
                cycle = " -> ".join([*chain[chain.index(step_id):], step_id])
                errors.append(f"Dependency cycle detected: {cycle}")
                return 0
            if known is not None:
                return known
            depth[step_id] = UNRESOLVED
            child_depths = [resolve(dep, [*chain, step_id]) for dep in deps.get(step_id, ())]
            depth[step_id] = 1 + max(child_depths, default=0)
            return depth[step_id]

        for step_id in deps:
            resolve(step_id, [])
            if errors:
                return errors

        deepest = max(depth.values(), default=0)
        if deepest > self.MAX_DEPTH:
            errors.append(f"Dependency chain depth {deepest} exceeds maximum {self.MAX_DEPTH}")
        return errors

    def _risk_warnings(self, plan: ExecutionPlan) -> list[str]:
        from deerflow.workspace.planner.risk_analyzer import RiskAnalyzer

        analyzed = RiskAnalyzer().analyze_plan(plan)
        if analyzed.order > RiskLevel.MEDIUM.order:
            return [f"Plan risk level is {analyzed.value}: execution requires explicit approval"]
        return []
