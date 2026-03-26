"""
PlanValidator — single ownership surface for PlanDocument structural + policy rules.

See Docs/architecture_freeze/VALIDATION_REGISTRY.md and SCHEMAS.md (Schema 1).
"""
from __future__ import annotations

from typing import Optional

from agent_v2.config import MAX_PLAN_STEPS as DEFAULT_MAX_PLAN_STEPS
from agent_v2.config import get_config
from agent_v2.schemas.plan import PlanDocument, PlanStep
from agent_v2.schemas.policies import ExecutionPolicy

ALLOWED_ACTIONS = frozenset({"search", "open_file", "edit", "shell", "finish"})
ALLOWED_TYPES = frozenset({"explore", "analyze", "modify", "validate", "finish"})
REQUIRED_INTENT_TYPES = frozenset({"modify", "analyze", "finish"})
class PlanValidationError(ValueError):
    """Raised when a PlanDocument violates SCHEMAS.md / policy rules."""


class PlanValidator:
    @staticmethod
    def validate_plan(
        plan: PlanDocument,
        *,
        policy: Optional[ExecutionPolicy] = None,
        task_mode: Optional[str] = None,
    ) -> None:
        """
        Cross-field and policy checks beyond Pydantic field types.

        Args:
            plan: Planner output to validate.
            policy: When set, enforces PlanStep.execution.max_attempts == max_retries_per_step.
            task_mode: When set to "read_only", prevents write actions (edit, run_tests, shell).
        """
        steps = plan.steps
        if not steps:
            raise PlanValidationError("Plan must have at least one step")

        max_steps_allowed = (
            int(policy.max_steps) if policy is not None else DEFAULT_MAX_PLAN_STEPS
        )
        if len(steps) > max_steps_allowed:
            raise PlanValidationError(
                f"Plan must have at most {max_steps_allowed} steps, got {len(steps)}"
            )

        step_ids = {s.step_id for s in steps}
        if len(step_ids) != len(steps):
            raise PlanValidationError("step_id values must be unique")

        if PlanValidator._dependency_graph_has_cycle(steps):
            raise PlanValidationError("Plan dependencies contain a cycle")

        PlanValidator._validate_dependencies_precede_step(steps)

        has_finish_action = False
        intent_types = set()

        for step in steps:
            PlanValidator._validate_step(step, step_ids, task_mode=task_mode)
            if step.action == "finish":
                has_finish_action = True
            intent_types.add(step.type)

        if not has_finish_action:
            raise PlanValidationError("Plan must include a step with action 'finish'")

        if not (intent_types & REQUIRED_INTENT_TYPES):
            raise PlanValidationError(
                "Plan must include at least one step with type in "
                "{modify, analyze, finish} per SCHEMAS.md Rule 4"
            )

        ordered = sorted(steps, key=lambda s: s.index)
        last = ordered[-1]
        if last.type != "finish":
            raise PlanValidationError(
                "Final step (by index) must have type 'finish' per SCHEMAS.md Rule 5"
            )
        if last.action != "finish":
            raise PlanValidationError("Final step must use action 'finish'")

        if not plan.risks:
            raise PlanValidationError("Plan must include at least one risk (SCHEMAS.md)")

        indices = sorted(s.index for s in steps)
        expected = list(range(1, len(steps) + 1))
        if indices != expected:
            raise PlanValidationError(
                f"Step indices must be exactly 1..{len(steps)} once each, got {indices}"
            )

        if policy is not None:
            for step in steps:
                if step.execution.max_attempts != policy.max_retries_per_step:
                    raise PlanValidationError(
                        "PlanStep.execution.max_attempts must match "
                        f"ExecutionPolicy.max_retries_per_step ({policy.max_retries_per_step}), "
                        f"got {step.execution.max_attempts} for step {step.step_id}"
                    )

    @staticmethod
    def _validate_step(step: PlanStep, step_ids: set[str], task_mode: Optional[str] = None) -> None:
        if step.action not in ALLOWED_ACTIONS:
            raise PlanValidationError(f"Invalid action {step.action!r} for step {step.step_id}")
        if step.type not in ALLOWED_TYPES:
            raise PlanValidationError(f"Invalid type {step.type!r} for step {step.step_id}")
        
        # LIVE-TEST-002: Enforce read-only mode constraints
        read_only_actions = get_config().planner.allowed_actions_read_only
        if task_mode == "read_only" and step.action not in read_only_actions:
            raise PlanValidationError(
                f"Step {step.step_id} uses write action {step.action!r} "
                f"but task_mode is 'read_only'. Only {read_only_actions} are allowed."
            )
        
        for dep in step.dependencies:
            if dep not in step_ids:
                raise PlanValidationError(
                    f"Step {step.step_id} depends on unknown step_id {dep!r}"
                )
            if dep == step.step_id:
                raise PlanValidationError(f"Step {step.step_id} must not depend on itself")

    @staticmethod
    def _dependency_graph_has_cycle(steps: list[PlanStep]) -> bool:
        """True if the dependency graph (step → its dependencies) has a cycle."""
        by_id = {s.step_id: s for s in steps}
        white, gray, black = 0, 1, 2
        color: dict[str, int] = {s.step_id: white for s in steps}

        def visit(sid: str) -> bool:
            if sid not in by_id:
                return False
            if color.get(sid, white) == gray:
                return True
            if color.get(sid, white) == black:
                return False
            color[sid] = gray
            for dep in by_id[sid].dependencies or []:
                if visit(dep):
                    return True
            color[sid] = black
            return False

        for s in steps:
            if visit(s.step_id):
                return True
        return False

    @staticmethod
    def _validate_dependencies_precede_step(steps: list[PlanStep]) -> None:
        """
        Each dependency must refer to a step with a strictly lower index (prior in plan order).
        Indices are assumed unique per PlanValidator index check (1..N).
        """
        by_id = {s.step_id: s for s in steps}
        for s in steps:
            my_idx = s.index
            for dep in s.dependencies or []:
                if dep not in by_id:
                    continue
                dep_idx = by_id[dep].index
                if dep_idx >= my_idx:
                    raise PlanValidationError(
                        f"Step {s.step_id!r} (index {my_idx}) depends on {dep!r} "
                        f"(index {dep_idx}); dependencies must reference only prior steps"
                    )
