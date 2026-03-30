"""
TaskPlannerService — thin decision provider ONLY.

Does not import runtime loops or tool execution modules.
"""

from __future__ import annotations

from typing import Protocol

from agent_v2.config import get_config
from agent_v2.schemas.planner_action import PlannerDecisionSnapshot
from agent_v2.schemas.planner_decision import PlannerDecision


class TaskPlannerService(Protocol):
    """Pure decision: snapshot in → PlannerDecision out (same type as the runtime loop)."""

    def decide(self, snapshot: PlannerDecisionSnapshot) -> PlannerDecision: ...


class RuleBasedTaskPlannerService:
    """
    Stub / baseline: prefer explore when instruction is non-empty; else stop.

    Replace with LLM-backed implementation later without changing the protocol.
    """

    def decide(self, snapshot: PlannerDecisionSnapshot) -> PlannerDecision:
        lo = (snapshot.last_loop_outcome or "").strip()
        if lo == "synthesize_completed":
            return PlannerDecision(type="stop", step=None, query=None, tool=None)
        if lo.startswith("explore_gate:"):
            return PlannerDecision(type="replan", step=None, query=None, tool=None)
        max_iter = get_config().planner_loop.max_act_controller_iterations
        if snapshot.act_controller_iteration_count >= max_iter:
            return PlannerDecision(type="synthesize", step=None, query=None, tool=None)
        inst = (snapshot.instruction or "").strip()
        if not inst:
            return PlannerDecision(type="stop", step=None, query=None, tool=None)
        return PlannerDecision(
            type="explore",
            step=None,
            query=inst,
            tool="explore",
        )


def default_task_planner_service() -> TaskPlannerService:
    return RuleBasedTaskPlannerService()
