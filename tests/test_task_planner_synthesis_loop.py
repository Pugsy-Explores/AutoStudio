"""Task planner + snapshot contract for ACT controller synthesize/stop (no infinite loop)."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

from agent_v2.config import (
    AgentV2Config,
    ChatPlanningConfig,
    ExplorationConfig,
    PlannerConfig,
    PlannerLoopConfig,
    PytestConfig,
)
from agent_v2.planning.decision_snapshot import (
    build_planner_decision_snapshot,
    plan_document_fingerprint,
)
from agent_v2.planning.task_planner import RuleBasedTaskPlannerService
from agent_v2.runtime.planner_task_runtime import _insufficiency_replan_context
from agent_v2.schemas.plan import (
    PlanDocument,
    PlanMetadata,
    PlanRisk,
    PlanSource,
    PlanStep,
    PlanStepExecution,
)
from agent_v2.schemas.planner_action import PlannerDecisionSnapshot


def _minimal_agent_v2_config(*, max_act: int = 256) -> AgentV2Config:
    return AgentV2Config(
        planner=PlannerConfig(
            allowed_actions_read_only=frozenset({"search", "open_file", "finish"}),
            allowed_actions_plan_safe=frozenset(
                {"search", "open_file", "run_tests", "shell", "finish"}
            ),
            strict_tool=False,
        ),
        exploration=ExplorationConfig(max_steps=5, allow_partial_for_plan_mode=False),
        pytest=PytestConfig(ignore_dirs=("artifacts",)),
        planner_loop=PlannerLoopConfig(
            controller_loop_enabled=True,
            max_sub_explorations_per_task=2,
            max_planner_controller_calls=16,
            max_act_controller_iterations=max_act,
            task_planner_shadow_loop=False,
            task_planner_authoritative_loop=False,
            planner_plan_body_only_when_task_planner_authoritative=False,
            enable_answer_validation=True,
            max_answer_validation_rounds_per_task=8,
            enable_answer_validation_llm=False,
        ),
        chat_planning=ChatPlanningConfig(
            enable_thin_task_planner=False,
            enable_exploration_stop_policy=False,
            skip_answer_synthesis_when_sufficient=False,
        ),
    )


def test_rule_based_planner_explores_with_validation_hint_when_validation_incomplete():
    svc = RuleBasedTaskPlannerService()
    snap = PlannerDecisionSnapshot(
        instruction="original instruction",
        last_loop_outcome="validation_incomplete",
        validation_retrieval_hint="find symbol Foo | check tests",
        has_pending_plan_work=False,
        act_controller_iteration_count=0,
    )
    d = svc.decide(snap)
    assert d.type == "explore"
    assert d.tool == "explore"
    assert "Foo" in (d.query or "")


def test_rule_based_planner_acts_when_validation_incomplete_and_pending_plan_work():
    svc = RuleBasedTaskPlannerService()
    snap = PlannerDecisionSnapshot(
        instruction="task",
        last_loop_outcome="validation_incomplete",
        validation_retrieval_hint="hint",
        has_pending_plan_work=True,
        act_controller_iteration_count=0,
    )
    d = svc.decide(snap)
    assert d.type == "act"


def test_build_planner_decision_snapshot_sets_validation_retrieval_hint():
    st = MagicMock()
    st.instruction = "x"
    st.metadata = {}
    st.context = {
        "validation_feedback": {
            "is_complete": False,
            "missing_context": ["alpha", "beta"],
            "issues": [],
            "confidence": "low",
        }
    }
    snap = build_planner_decision_snapshot(st, None, rolling_conversation_summary="")
    assert "alpha" in snap.validation_retrieval_hint
    assert "beta" in snap.validation_retrieval_hint


def test_rule_based_planner_stops_when_last_loop_outcome_is_synthesize_completed():
    svc = RuleBasedTaskPlannerService()
    snap = PlannerDecisionSnapshot(
        instruction="do something",
        last_loop_outcome="synthesize_completed",
        act_controller_iteration_count=99,
    )
    d = svc.decide(snap)
    assert d.type == "stop"


def test_rule_based_planner_forces_synthesize_at_max_act_iterations():
    svc = RuleBasedTaskPlannerService()
    snap = PlannerDecisionSnapshot(
        instruction="task",
        last_loop_outcome="",
        act_controller_iteration_count=10,
    )
    cfg = _minimal_agent_v2_config(max_act=10)
    with patch("agent_v2.planning.task_planner.get_config", return_value=cfg):
        d = svc.decide(snap)
    assert d.type == "synthesize"


def test_build_planner_decision_snapshot_consumes_last_loop_outcome_from_metadata():
    st = MagicMock()
    st.instruction = "x"
    st.metadata = {"task_planner_last_loop_outcome": "synthesize_completed"}
    st.context = {}
    snap = build_planner_decision_snapshot(st, None, rolling_conversation_summary="")
    assert snap.last_loop_outcome == "synthesize_completed"
    assert "task_planner_last_loop_outcome" not in st.metadata


def test_build_planner_decision_snapshot_includes_act_controller_iteration_count():
    st = MagicMock()
    st.instruction = "x"
    st.metadata = {"act_controller_iteration_count": 7}
    st.context = {}
    snap = build_planner_decision_snapshot(st, None, rolling_conversation_summary="")
    assert snap.act_controller_iteration_count == 7
    assert st.metadata.get("act_controller_iteration_count") == 7


def test_rule_based_planner_synthesizes_on_explore_blocked_not_explore():
    svc = RuleBasedTaskPlannerService()
    snap = PlannerDecisionSnapshot(
        instruction="same query forever",
        last_loop_outcome="explore_blocked:signals",
        act_controller_iteration_count=0,
    )
    d = svc.decide(snap)
    assert d.type == "synthesize"


def test_rule_based_planner_synthesizes_on_replan_no_progress():
    svc = RuleBasedTaskPlannerService()
    snap = PlannerDecisionSnapshot(
        instruction="task",
        last_loop_outcome="replan_no_progress",
        act_controller_iteration_count=0,
    )
    d = svc.decide(snap)
    assert d.type == "synthesize"


def test_rule_based_legacy_explore_gate_prefix_still_synthesizes():
    svc = RuleBasedTaskPlannerService()
    snap = PlannerDecisionSnapshot(
        instruction="x",
        last_loop_outcome="explore_gate:signals",
        act_controller_iteration_count=0,
    )
    d = svc.decide(snap)
    assert d.type == "synthesize"


def test_build_planner_decision_snapshot_consumes_explore_block_details_with_outcome():
    st = MagicMock()
    st.instruction = "x"
    st.metadata = {
        "task_planner_last_loop_outcome": "explore_blocked:duplicate_query",
        "explore_block_details": {"gaps_count": 2, "confidence": "high"},
    }
    st.context = {}
    snap = build_planner_decision_snapshot(st, None, rolling_conversation_summary="")
    assert snap.last_loop_outcome == "explore_blocked:duplicate_query"
    assert snap.explore_block_details == {"gaps_count": 2, "confidence": "high"}
    assert "task_planner_last_loop_outcome" not in st.metadata
    assert "explore_block_details" not in st.metadata


def test_build_planner_decision_snapshot_includes_last_plan_hash_when_plan_doc_given():
    st = MagicMock()
    st.instruction = "x"
    st.metadata = {}
    st.context = {}
    s = PlanStep(
        step_id="s1",
        index=1,
        type="finish",
        goal="g",
        action="finish",
        inputs={},
        execution=PlanStepExecution(),
    )
    pd = PlanDocument(
        plan_id="p1",
        instruction="i",
        understanding="u",
        sources=[PlanSource(type="other", ref="r", summary="s")],
        steps=[s],
        risks=[PlanRisk(risk="r", impact="low", mitigation="m")],
        completion_criteria=["c"],
        metadata=PlanMetadata(created_at="2026-01-01T00:00:00Z", version=1),
    )
    snap = build_planner_decision_snapshot(st, None, rolling_conversation_summary="", plan_doc=pd)
    assert snap.last_plan_hash == plan_document_fingerprint(pd)


def test_plan_fingerprint_detects_identical_plans():
    s = PlanStep(
        step_id="s1",
        index=1,
        type="finish",
        goal="g",
        action="finish",
        inputs={},
        execution=PlanStepExecution(),
    )
    pd = PlanDocument(
        plan_id="p1",
        instruction="i",
        understanding="u",
        sources=[PlanSource(type="other", ref="r", summary="s")],
        steps=[s],
        risks=[PlanRisk(risk="r", impact="low", mitigation="m")],
        completion_criteria=["c"],
        metadata=PlanMetadata(created_at="2026-01-01T00:00:00Z", version=1),
    )
    assert plan_document_fingerprint(pd) == plan_document_fingerprint(pd)


def test_insufficiency_replan_context_includes_explore_block_from_metadata():
    s = PlanStep(
        step_id="s1",
        index=1,
        type="finish",
        goal="g",
        action="finish",
        inputs={},
        execution=PlanStepExecution(),
    )
    pd = PlanDocument(
        plan_id="p1",
        instruction="i",
        understanding="u",
        sources=[PlanSource(type="other", ref="r", summary="s")],
        steps=[s],
        risks=[PlanRisk(risk="r", impact="low", mitigation="m")],
        completion_criteria=["c"],
        metadata=PlanMetadata(created_at="2026-01-01T00:00:00Z", version=1),
    )
    st = MagicMock()
    st.metadata = {
        "task_planner_last_loop_outcome": "explore_blocked:signals",
        "explore_block_details": {"gaps_count": 0, "confidence": "high"},
    }
    ctx = _insufficiency_replan_context(pd, "instr", st)
    assert ctx.task_control_last_outcome == "explore_blocked:signals"
    assert ctx.explore_block_details == {"gaps_count": 0, "confidence": "high"}
    assert "explore_blocked" in ctx.failure_context.error.message
