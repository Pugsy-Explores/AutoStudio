"""Skip answer synthesis when understanding is sufficient (config flag)."""

from __future__ import annotations

from unittest.mock import patch

from agent_v2.config import ChatPlanningConfig, PlannerLoopConfig, PlannerConfig, PytestConfig, ExplorationConfig, AgentV2Config
from agent_v2.schemas.exploration import ExplorationSummary
from agent_v2.schemas.final_exploration import ExplorationAdapterTrace, FinalExplorationSchema
from agent_v2.schemas.exploration import ExplorationResultMetadata
from agent_v2.state.agent_state import AgentState


def _make_cfg(*, skip: bool) -> AgentV2Config:
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
            max_act_controller_iterations=256,
            task_planner_shadow_loop=False,
            task_planner_authoritative_loop=False,
            planner_plan_body_only_when_task_planner_authoritative=False,
        ),
        chat_planning=ChatPlanningConfig(
            enable_thin_task_planner=False,
            enable_exploration_stop_policy=False,
            skip_answer_synthesis_when_sufficient=skip,
        ),
    )


def _sufficient_fe() -> FinalExplorationSchema:
    summ = ExplorationSummary(
        overall="o",
        key_findings=[],
        knowledge_gaps=[],
        knowledge_gaps_empty_reason="none",
    )
    md = ExplorationResultMetadata(total_items=0, created_at="2026-01-01T00:00:00Z")
    return FinalExplorationSchema(
        exploration_id="x",
        instruction="i",
        status="complete",
        evidence=[],
        relationships=[],
        exploration_summary=summ,
        metadata=md,
        confidence="high",
        trace=ExplorationAdapterTrace(llm_used=False, synthesis_success=True),
    )


def test_maybe_synthesize_skips_when_flag_and_sufficient():
    state = AgentState(instruction="q")
    fe = _sufficient_fe()
    cfg = _make_cfg(skip=True)
    with patch("agent_v2.exploration.answer_synthesizer.ENABLE_ANSWER_SYNTHESIS", True):
        with patch("agent_v2.exploration.answer_synthesizer.get_config", return_value=cfg):
            with patch("agent_v2.exploration.answer_synthesizer.synthesize_answer") as syn:
                from agent_v2.exploration.answer_synthesizer import maybe_synthesize_to_state

                maybe_synthesize_to_state(state, fe, None)
                syn.assert_not_called()


def test_maybe_synthesize_runs_when_skip_off():
    state = AgentState(instruction="q")
    fe = _sufficient_fe()
    cfg = _make_cfg(skip=False)
    with patch("agent_v2.exploration.answer_synthesizer.ENABLE_ANSWER_SYNTHESIS", True):
        with patch("agent_v2.exploration.answer_synthesizer.get_config", return_value=cfg):
            with patch("agent_v2.exploration.answer_synthesizer.synthesize_answer") as syn:
                from agent_v2.exploration.answer_synthesizer import maybe_synthesize_to_state
                from agent_v2.schemas.answer_synthesis import AnswerSynthesisResult

                syn.return_value = AnswerSynthesisResult(synthesis_success=False)
                maybe_synthesize_to_state(state, fe, None)
                syn.assert_called_once()
