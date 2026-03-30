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
from agent_v2.planning.decision_snapshot import build_planner_decision_snapshot
from agent_v2.planning.task_planner import RuleBasedTaskPlannerService
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
        ),
        chat_planning=ChatPlanningConfig(
            enable_thin_task_planner=False,
            enable_exploration_stop_policy=False,
            skip_answer_synthesis_when_sufficient=False,
        ),
    )


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
