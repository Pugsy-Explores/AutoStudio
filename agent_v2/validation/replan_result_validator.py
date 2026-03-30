"""
ReplanResult structural checks — VALIDATION_REGISTRY.md (sibling to PlanValidator).

Pydantic enforces shapes; this module enforces cross-field rules from SCHEMAS.md
(e.g. new_plan nullability vs status).
"""
from __future__ import annotations

from agent_v2.schemas.replan import ReplanResult

from .plan_validator import PlanValidationError


class ReplanResultValidator:
    @staticmethod
    def validate_replan_result(result: ReplanResult) -> None:
        if result.status == "success":
            if result.new_plan is None:
                raise PlanValidationError("ReplanResult with status=success requires new_plan")
        elif result.new_plan is not None:
            raise PlanValidationError("ReplanResult with status=failed must have new_plan=None")
