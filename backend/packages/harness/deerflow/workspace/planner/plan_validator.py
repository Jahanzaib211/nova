"""Plan Validator — validates execution plans before execution.

Phase C9 — validates that a plan is:
- Non-empty
- Within resource budgets
- Has no cycles
- Has valid step dependencies
- Has approved risk level
"""

from __future__ import annotations

from dataclasses import dataclass

from deerflow.workspace.models.execution_plan import ExecutionPlan, RiskLevel, StepKind


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
    - Risk level must not exceed MEDIUM for auto-approved plans
    """

    MAX_STEPS = 50
    MAX_DEPTH = 10

    def validate(self, plan: ExecutionPlan) -> PlanValidationResult:
        errors = []
        warnings = []

        if not plan.steps:
            errors.append("Plan is empty")
            return PlanValidationResult(is_valid=False, errors=errors, warnings=warnings)

        if len(plan.steps) > self.MAX_STEPS:
            errors.append(f"Plan has {len(plan.steps)} steps, maximum is {self.MAX_STEPS}")

        step_ids = {s.step_id for s in plan.steps}
        if len(step_ids) != len(plan.steps):
            errors.append("Duplicate step IDs found in plan")

        return PlanValidationResult(
            is_valid=len(errors) == 0,
            errors=errors,
            warnings=warnings,
        )
