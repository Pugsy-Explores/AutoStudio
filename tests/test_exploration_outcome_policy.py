from __future__ import annotations

from agent_v2.config import ChatPlanningConfig, PlannerLoopConfig, PlannerConfig, PytestConfig, ExplorationConfig, AgentV2Config
from agent_v2.memory.task_working_memory import TaskWorkingMemory
from agent_v2.planning.exploration_outcome_policy import (
    normalize_understanding,
    should_stop_after_exploration,
    sub_exploration_allowed,
)
from agent_v2.schemas.exploration import ExplorationSummary
from agent_v2.schemas.final_exploration import (
    ExplorationAdapterTrace,
    FinalExplorationSchema,
)
from agent_v2.schemas.exploration import ExplorationResultMetadata


def _fe(conf: str, gaps: list[str]) -> FinalExplorationSchema:
    summ = ExplorationSummary(
        overall="o",
        key_findings=[],
        knowledge_gaps=gaps,
        knowledge_gaps_empty_reason=None if gaps else "none",
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
        confidence=conf,  # type: ignore[arg-type]
        trace=ExplorationAdapterTrace(llm_used=False, synthesis_success=True),
    )


def test_normalize_understanding_sufficient():
    fe = _fe("high", [])
    assert normalize_understanding(fe) == "sufficient"


def test_normalize_understanding_insufficient():
    fe = _fe("low", [])
    assert normalize_understanding(fe) == "insufficient"


def _cfg(*, stop_on: bool) -> AgentV2Config:
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
            enable_answer_validation=True,
            max_answer_validation_rounds_per_task=8,
            enable_answer_validation_llm=False,
        ),
        chat_planning=ChatPlanningConfig(
            enable_thin_task_planner=False,
            enable_exploration_stop_policy=stop_on,
            skip_answer_synthesis_when_sufficient=False,
        ),
    )


def test_should_stop_when_sufficient_and_policy_on():
    fe = _fe("high", [])
    wm = TaskWorkingMemory()
    stop, reason = should_stop_after_exploration(
        fe, wm, chat=_cfg(stop_on=True).chat_planning
    )
    assert stop and reason == "sufficient_understanding"


def test_should_stop_policy_off():
    fe = _fe("high", [])
    wm = TaskWorkingMemory()
    stop, _ = should_stop_after_exploration(
        fe, wm, chat=_cfg(stop_on=False).chat_planning
    )
    assert not stop


def test_sub_exploration_allowed_matches_legacy_when_policy_off():
    fe = _fe("medium", [])  # no gaps, not low -> legacy gate False
    wm = TaskWorkingMemory()
    assert not sub_exploration_allowed(fe, wm, cfg=_cfg(stop_on=False))
