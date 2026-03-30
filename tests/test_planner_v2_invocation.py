from __future__ import annotations

import pytest

from agent_v2.planning.planner_v2_invocation import (
    plan_document_has_runnable_work,
    plan_document_valid_for_v2_gate,
    should_call_planner_v2,
)
from agent_v2.schemas.plan import (
    PlanDocument,
    PlanMetadata,
    PlanRisk,
    PlanSource,
    PlanStep,
    PlanStepExecution,
)
from agent_v2.schemas.planner_decision import PlannerDecision


def _minimal_plan(steps: list[PlanStep]) -> PlanDocument:
    return PlanDocument(
        plan_id="p1",
        instruction="i",
        understanding="u",
        sources=[PlanSource(type="other", ref="r", summary="s")],
        steps=steps,
        risks=[PlanRisk(risk="r", impact="low", mitigation="m")],
        completion_criteria=["c"],
        metadata=PlanMetadata(created_at="2026-01-01T00:00:00Z", version=1),
    )


def test_plan_document_valid_for_v2_gate():
    assert plan_document_valid_for_v2_gate(None) is False
    assert plan_document_valid_for_v2_gate(_minimal_plan([])) is False
    s = PlanStep(
        step_id="s1",
        index=1,
        type="finish",
        goal="g",
        action="finish",
        inputs={},
        execution=PlanStepExecution(),
    )
    assert plan_document_valid_for_v2_gate(_minimal_plan([s])) is True


def test_plan_document_has_runnable_work():
    s_done = PlanStep(
        step_id="s1",
        index=1,
        type="finish",
        goal="g",
        action="finish",
        inputs={},
        execution=PlanStepExecution(status="completed"),
    )
    assert plan_document_has_runnable_work(_minimal_plan([s_done])) is False
    s_run = PlanStep(
        step_id="s1",
        index=1,
        type="finish",
        goal="g",
        action="finish",
        inputs={},
        execution=PlanStepExecution(status="in_progress"),
    )
    assert plan_document_has_runnable_work(_minimal_plan([s_run])) is True


def test_should_call_planner_v2_task_decision():
    assert (
        should_call_planner_v2(
            context="task_decision",
            decision=PlannerDecision(type="explore", query="q"),
            plan_valid=True,
        )
        is False
    )
    assert (
        should_call_planner_v2(
            context="task_decision",
            decision=PlannerDecision(type="plan", query=None),
            plan_valid=True,
        )
        is True
    )
    assert (
        should_call_planner_v2(
            context="task_decision",
            decision=PlannerDecision(type="replan"),
            plan_valid=True,
        )
        is True
    )
    assert (
        should_call_planner_v2(
            context="task_decision",
            decision=PlannerDecision(type="stop"),
            plan_valid=False,
        )
        is False
    )


def test_should_call_planner_v2_bootstrap_and_merge():
    assert should_call_planner_v2(context="bootstrap", plan_valid=False) is True
    assert should_call_planner_v2(context="bootstrap", plan_valid=True) is False
    assert should_call_planner_v2(context="post_exploration_merge") is True
    assert should_call_planner_v2(context="failure_or_insufficiency_replan") is True
    assert should_call_planner_v2(context="progress_refresh") is True


def test_task_decision_requires_decision():
    with pytest.raises(ValueError, match="requires decision"):
        should_call_planner_v2(context="task_decision")
