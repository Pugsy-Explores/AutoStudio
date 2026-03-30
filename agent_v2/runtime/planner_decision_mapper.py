"""
Maps validated PlanDocument (including PlannerControllerOutput) → PlannerDecision.

Single boundary: orchestration must not read plan_doc.controller elsewhere.
"""

from __future__ import annotations

from typing import Any

from agent_v2.schemas.plan import PlanDocument, PlannerControllerOutput
from agent_v2.schemas.planner_decision import PlannerDecision


def _engine_tool_for_decision(eng: Any) -> str | None:
    t = getattr(eng, "tool", None)
    if t is None or str(t).strip() == "" or str(t).strip() == "none":
        return None
    return str(t).strip()


def _decision_from_engine(plan_doc: PlanDocument) -> PlannerDecision | None:
    eng = plan_doc.engine
    if eng is None:
        return None
    d = (eng.decision or "").strip().lower()
    tv = _engine_tool_for_decision(eng)
    if d == "explore":
        q = (eng.query or "").strip()
        return PlannerDecision(
            type="explore",
            step=None,
            query=q if q else None,
            tool=tv or "explore",
        )
    if d == "replan":
        return PlannerDecision(type="replan", step=None, query=None, tool=tv)
    if d == "stop":
        return PlannerDecision(type="stop", step=None, query=None, tool=tv)
    if d == "act":
        return PlannerDecision(type="act", step=None, query=None, tool=tv)
    if d == "synthesize":
        return PlannerDecision(type="synthesize", step=None, query=None, tool=tv or "synthesize")
    if d == "plan":
        q = (eng.query or "").strip()
        return PlannerDecision(
            type="plan",
            step=None,
            query=q if q else None,
            tool=tv,
        )
    return None


def plan_document_has_no_pending_work(plan_doc: PlanDocument) -> bool:
    """
    True when no executor step should run: empty plan or all steps completed.

    Used so the runtime emits explicit STOP instead of calling the executor with nothing to do.
    """
    steps = plan_doc.steps or []
    if not steps:
        return True
    return all(s.execution.status == "completed" for s in steps)


def planner_decision_from_plan_document(plan_doc: PlanDocument) -> PlannerDecision:
    """
    Deterministic mapping from planner JSON (already validated as PlanDocument).

    Precedence:
    1) plan.engine (decision-first) → act | explore | replan | stop
    2) No pending work → stop
    3) controller.action (legacy) → explore | replan | stop | continue→act
    """
    from_engine = _decision_from_engine(plan_doc)
    if from_engine is not None:
        return from_engine

    if plan_document_has_no_pending_work(plan_doc):
        return PlannerDecision(type="stop", step=None, query=None)

    ctrl: PlannerControllerOutput | None = plan_doc.controller
    if ctrl is None:
        return PlannerDecision(type="act", step=None, query=None)
    action = (ctrl.action or "continue").strip().lower()
    if action == "explore":
        q = (ctrl.exploration_query or "").strip()
        return PlannerDecision(type="explore", step=None, query=q if q else None)
    if action == "replan":
        return PlannerDecision(type="replan", step=None, query=None)
    if action == "stop":
        return PlannerDecision(type="stop", step=None, query=None)
    # continue
    return PlannerDecision(type="act", step=None, query=None)
