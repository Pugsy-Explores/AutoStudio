"""
Plan resolver: router decides, planner plans.

Per docs (phase.md, ROUTING_ARCHITECTURE_REPORT.md):
- Instruction router classifies before planner when ENABLE_INSTRUCTION_ROUTER=1
- CODE_SEARCH / CODE_EXPLAIN / INFRA → single-step plan, skip planner (30–60% fewer planner calls)
- CODE_EDIT / GENERAL → planner produces multi-step plan

Categories: CODE_SEARCH, CODE_EDIT, CODE_EXPLAIN, INFRA, GENERAL
Planner actions: SEARCH, EDIT, EXPLAIN, INFRA
"""

import logging

from agent.observability.trace_logger import trace_stage
from config.router_config import ENABLE_INSTRUCTION_ROUTER
from planner.planner import plan

logger = logging.getLogger(__name__)


def get_plan(
    instruction: str,
    trace_id: str | None = None,
    log_event_fn=None,
    retry_context: dict | None = None,
) -> dict:
    """
    Resolve plan: use instruction router when enabled, else planner.

    When ENABLE_INSTRUCTION_ROUTER=1:
    - CODE_SEARCH → single SEARCH step (skip planner)
    - CODE_EXPLAIN → single EXPLAIN step (skip planner)
    - INFRA → single INFRA step (skip planner)
    - CODE_EDIT / GENERAL → planner

    When disabled: always use planner.

    Phase 5: retry_context (previous_attempts, critic_feedback) is passed to planner when provided.
    """
    if not ENABLE_INSTRUCTION_ROUTER:
        if trace_id:
            with trace_stage(trace_id, "planner") as summary:
                plan_result = plan(instruction, retry_context=retry_context)
                summary["instruction"] = (instruction or "")[:200]
                summary["number_of_steps"] = len(plan_result.get("steps", []))
                summary["actions"] = [s.get("action") for s in plan_result.get("steps", [])]
            return plan_result
        return plan(instruction, retry_context=retry_context)

    from agent.routing.instruction_router import route_instruction

    decision = route_instruction(instruction)
    category = decision.category

    if log_event_fn and trace_id:
        try:
            log_event_fn(trace_id, "instruction_router", {"category": category, "confidence": decision.confidence})
        except Exception as e:
            logger.debug("[plan_resolver] log_event skipped: %s", e)

    if category == "CODE_SEARCH":
        plan_result = {
            "steps": [
                {"id": 1, "action": "SEARCH", "description": instruction, "reason": "Routed by instruction router"}
            ],
        }
        if trace_id:
            with trace_stage(trace_id, "planner") as summary:
                summary["instruction"] = (instruction or "")[:200]
                summary["number_of_steps"] = 1
                summary["actions"] = ["SEARCH"]
        return plan_result
    if category == "CODE_EXPLAIN":
        plan_result = {
            "steps": [
                {"id": 1, "action": "EXPLAIN", "description": instruction, "reason": "Routed by instruction router"}
            ],
        }
        if trace_id:
            with trace_stage(trace_id, "planner") as summary:
                summary["instruction"] = (instruction or "")[:200]
                summary["number_of_steps"] = 1
                summary["actions"] = ["EXPLAIN"]
        return plan_result
    if category == "INFRA":
        plan_result = {
            "steps": [
                {"id": 1, "action": "INFRA", "description": instruction, "reason": "Routed by instruction router"}
            ],
        }
        if trace_id:
            with trace_stage(trace_id, "planner") as summary:
                summary["instruction"] = (instruction or "")[:200]
                summary["number_of_steps"] = 1
                summary["actions"] = ["INFRA"]
        return plan_result

    # CODE_EDIT or GENERAL: use planner
    if trace_id:
        with trace_stage(trace_id, "planner") as summary:
            plan_result = plan(instruction, retry_context=retry_context)
            summary["instruction"] = (instruction or "")[:200]
            summary["number_of_steps"] = len(plan_result.get("steps", []))
            summary["actions"] = [s.get("action") for s in plan_result.get("steps", [])]
        return plan_result
    return plan(instruction, retry_context=retry_context)
