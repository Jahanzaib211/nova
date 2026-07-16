"""Risk Analyzer — evaluates risk level of execution steps.

Phase C9 — each step in an execution plan is analyzed for risk
before execution.  Risk levels map to execution policies:
- LOW: execute without guard
- MEDIUM: log and confirm
- HIGH: require explicit approval
- CRITICAL: block and escalate
"""

from __future__ import annotations

from dataclasses import dataclass

from deerflow.workspace.models.execution_plan import ExecutionPlan, ExecutionStep, RiskLevel, StepKind


@dataclass
class RiskAnalyzer:
    """Analyze execution steps for risk level.

    Considers:
    - Step kind (shell > read > search)
    - Destructive flag
    - Path scope (project root vs system)
    - Command type (sudo, rm, git reset hard)
    - Target (production data, system files)
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

        if self._is_git_destructive(step):
            risk = self._raise(risk, RiskLevel.HIGH)
        if self._is_dangerous_command(step):
            risk = self._raise(risk, RiskLevel.CRITICAL)

        return risk

    def analyze_plan(self, plan: ExecutionPlan) -> RiskLevel:
        """Return the maximum risk level across all steps."""
        if not plan.steps:
            return RiskLevel.LOW
        risks = [self.analyze_step(s) for s in plan.steps]
        return max(risks, key=lambda r: r.order)

    def approve_plan(self, plan: ExecutionPlan) -> bool:
        """Return True if the plan is auto-approvable (all steps LOW risk)."""
        return self.analyze_plan(plan) <= RiskLevel.MEDIUM

    def _is_git_destructive(self, step: ExecutionStep) -> bool:
        if not step.argv:
            return False
        cmd = " ".join(str(a) for a in step.argv)
        return any(kw in cmd for kw in ["git reset --hard", "git clean -fd", "git push --force"])

    def _is_dangerous_command(self, step: ExecutionStep) -> bool:
        if not step.argv:
            return False
        cmd = " ".join(str(a) for a in step.argv)
        return any(kw in cmd for kw in ["rm -rf /", "dd if=", "mkfs", ":(){:|:&};:"])
