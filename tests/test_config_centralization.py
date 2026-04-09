from __future__ import annotations

from unittest.mock import MagicMock, patch

import agent_v2.config as cfg_mod
from agent_v2.config import (
    AgentV2Config,
    ChatPlanningConfig,
    ExplorationConfig,
    PlannerConfig,
    PlannerLoopConfig,
    PytestConfig,
    get_config,
    validate_config,
)
from agent_v2.runtime.mode_manager import ModeManager
from agent_v2.schemas.plan import (
    PlanDocument,
    PlanMetadata,
    PlanRisk,
    PlanSource,
    PlanStep,
)
from agent_v2.state.agent_state import AgentState


def test_validate_config_rejects_write_actions_in_read_only_policy():
    bad_cfg = AgentV2Config(
        planner=PlannerConfig(
            allowed_actions_read_only=frozenset({"search", "edit"}),
            allowed_actions_plan_safe=frozenset(
                {"search", "open_file", "run_tests", "shell", "finish"}
            ),
            strict_tool=False,
        ),
        exploration=ExplorationConfig(max_steps=5, allow_partial_for_plan_mode=False),
        pytest=PytestConfig(ignore_dirs=("artifacts",)),
        planner_loop=PlannerLoopConfig(
            controller_loop_enabled=False,
            max_sub_explorations_per_task=2,
            max_planner_controller_calls=16,
            max_act_controller_iterations=256,
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
    try:
        validate_config(bad_cfg)
        assert False, "Expected validate_config to reject write action in read-only policy"
    except ValueError as exc:
        assert "exclude write actions" in str(exc)


def test_default_config_parity_read_only_policy():
    cfg = get_config()
    assert cfg.planner.allowed_actions_read_only == frozenset({"search", "open_file", "finish"})
    assert cfg.planner_loop.controller_loop_enabled is True


def test_plan_mode_allows_partial_exploration_when_config_enabled():
    original_cfg = cfg_mod._CONFIG
    cfg_mod._CONFIG = AgentV2Config(
        planner=original_cfg.planner,
        exploration=ExplorationConfig(
            max_steps=original_cfg.exploration.max_steps,
            allow_partial_for_plan_mode=True,
        ),
        pytest=original_cfg.pytest,
        planner_loop=original_cfg.planner_loop,
        chat_planning=original_cfg.chat_planning,
    )
    try:
        mock_planner = MagicMock()
        mock_plan = PlanDocument(
            plan_id="p1",
            instruction="i",
            understanding="u",
            sources=[PlanSource(type="other", ref="r", summary="s")],
            steps=[
                PlanStep(
                    step_id="s1",
                    index=1,
                    type="finish",
                    goal="g",
                    action="finish",
                    inputs={},
                ),
            ],
            risks=[PlanRisk(risk="r", impact="low", mitigation="m")],
            completion_criteria=["c"],
            metadata=PlanMetadata(created_at="2026-01-01T00:00:00Z", version=1),
        )
        mock_planner.plan.return_value = mock_plan

        mock_exp = MagicMock()
        mock_exp.query_intent = None  # avoid MagicMock auto-attr → invalid PlannerPlanContext
        mock_exp.exploration_summary.overall = "partial but useful"
        mock_exp.model_dump.return_value = {"exploration_id": "e1"}
        mock_exp.metadata = MagicMock()
        mock_exp.metadata.completion_status = "incomplete"
        mock_exp.metadata.termination_reason = "max_steps"
        mock_exp.metadata.engine_loop_steps = 0
        mock_er = MagicMock()
        mock_er.run.return_value = mock_exp

        manager = ModeManager(mock_er, mock_planner, plan_executor=MagicMock())
        state = AgentState(instruction="plan only")
        with patch("agent_v2.runtime.planner_task_runtime.maybe_synthesize_to_state"):
            manager.run(state, mode="plan_legacy")
        assert mock_planner.plan.called
    finally:
        cfg_mod._CONFIG = original_cfg
