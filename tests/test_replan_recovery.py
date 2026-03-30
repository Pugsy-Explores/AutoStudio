"""Tests for replan recovery helpers and replanner integration."""

from unittest.mock import patch

import pytest

from agent.memory.state import AgentState
from agent.memory.step_result import StepResult
from agent.models.model_types import ModelType
from agent.orchestrator.replan_recovery import (
    RECOVERY_GENERIC_FAILURE,
    RECOVERY_NEED_BETTER_BUILD_CONTEXT,
    RECOVERY_NEED_IMPLEMENTATION_SEARCH,
    RECOVERY_NEED_NON_TEST_CODE,
    RECOVERY_NEED_SEARCH_BEFORE_EXPLAIN,
    SIGNAL_UNKNOWN,
    build_replan_failure_context,
    classify_replan_recovery_mode,
    normalize_replan_error_signal,
    refine_search_description_for_recovery,
    repair_replan_steps_for_recovery,
    searches_are_near_duplicates,
)
from agent.orchestrator.replanner import replan


def _state(
    instruction: str,
    *,
    steps: list[dict],
    step_results: list[StepResult] | None = None,
    context: dict | None = None,
) -> AgentState:
    ctx = {"dominant_artifact_mode": "code", "lane_violations": []}
    if context:
        ctx.update(context)
    return AgentState(
        instruction=instruction,
        current_plan={"plan_id": "p1", "steps": steps},
        completed_steps=[],
        step_results=step_results or [],
        context=ctx,
    )


# --- normalize / classify ---


def test_normalize_reason_code_insufficient_substantive():
    assert normalize_replan_error_signal(None, "insufficient_substantive_context") == "insufficient_substantive_context"


def test_normalize_legacy_unknown():
    assert normalize_replan_error_signal("some random failure", None) == SIGNAL_UNKNOWN


def test_classify_insufficient_substantive_explain():
    fc = {
        "dominant_artifact_mode": "code",
        "failed_action": "EXPLAIN",
        "error_signal": "insufficient_substantive_context",
        "search_quality": None,
        "build_context_empty": False,
    }
    assert classify_replan_recovery_mode(fc) == RECOVERY_NEED_IMPLEMENTATION_SEARCH


def test_classify_insufficient_grounding():
    fc = {
        "dominant_artifact_mode": "code",
        "failed_action": "EXPLAIN",
        "error_signal": "insufficient_grounding",
        "search_quality": None,
        "build_context_empty": False,
    }
    assert classify_replan_recovery_mode(fc) == RECOVERY_NEED_SEARCH_BEFORE_EXPLAIN


def test_classify_search_weak():
    fc = {
        "dominant_artifact_mode": "code",
        "failed_action": "SEARCH",
        "error_signal": "unknown",
        "search_quality": "weak",
        "build_context_empty": False,
    }
    assert classify_replan_recovery_mode(fc) == RECOVERY_NEED_IMPLEMENTATION_SEARCH


def test_classify_test_only():
    fc = {
        "dominant_artifact_mode": "code",
        "failed_action": "SEARCH",
        "error_signal": "test_only_context",
        "search_quality": None,
        "build_context_empty": False,
    }
    assert classify_replan_recovery_mode(fc) == RECOVERY_NEED_NON_TEST_CODE


def test_classify_build_context_empty():
    fc = {
        "dominant_artifact_mode": "code",
        "failed_action": "BUILD_CONTEXT",
        "error_signal": "unknown",
        "search_quality": None,
        "build_context_empty": True,
    }
    assert classify_replan_recovery_mode(fc) == RECOVERY_NEED_BETTER_BUILD_CONTEXT


def test_classify_legacy_unknown_is_generic():
    fc = {
        "dominant_artifact_mode": "code",
        "failed_action": "EDIT",
        "error_signal": SIGNAL_UNKNOWN,
        "search_quality": None,
        "build_context_empty": False,
    }
    assert classify_replan_recovery_mode(fc) == RECOVERY_GENERIC_FAILURE


def test_classify_docs_lane_is_generic():
    fc = {
        "dominant_artifact_mode": "docs",
        "failed_action": "EXPLAIN",
        "error_signal": "insufficient_substantive_context",
        "search_quality": "weak",
        "build_context_empty": False,
    }
    assert classify_replan_recovery_mode(fc) == RECOVERY_GENERIC_FAILURE


def test_classify_search_candidates_empty_build_no_prior_search():
    fc = {
        "dominant_artifact_mode": "code",
        "failed_action": "SEARCH_CANDIDATES",
        "error_signal": "unknown",
        "search_quality": None,
        "build_context_empty": True,
        "recent_searches": [],
    }
    assert classify_replan_recovery_mode(fc) == RECOVERY_NEED_BETTER_BUILD_CONTEXT


# --- near duplicates ---


def test_searches_are_near_duplicates_jaccard():
    a = "find dispatch implementation in agent execution module"
    b = "locate dispatch implementation inside agent execution module"
    assert searches_are_near_duplicates(a, b) is True


def test_searches_are_near_duplicates_short_exact_only():
    assert searches_are_near_duplicates("foo bar", "foo bar") is True
    assert searches_are_near_duplicates("foo", "foo baz qux") is False


# --- refinement ---


def test_refine_instruction_first_not_only_failed_desc():
    h = refine_search_description_for_recovery(
        original_instruction="How does dispatch connect to StepExecutor",
        failed_step_desc="Search dispatch",
        recovery_mode=RECOVERY_NEED_IMPLEMENTATION_SEARCH,
        prior_search_descs=[],
        attempt_n=1,
    )
    assert "dispatch" in h.lower() or "How does" in h
    assert "implementation" in h.lower() or "non-test" in h.lower()


def test_refine_escalation_attempt_2_adds_entrypoint_terms():
    h1 = refine_search_description_for_recovery(
        original_instruction="Explain package entrypoint and settings",
        failed_step_desc="Search implementation code for package",
        recovery_mode=RECOVERY_NEED_IMPLEMENTATION_SEARCH,
        prior_search_descs=["Search implementation code for package"],
        attempt_n=2,
    )
    assert "__main__" in h1 or "__init__" in h1 or "cli" in h1.lower()


# --- repair ---


def test_repair_collapses_duplicate_search_before_explain():
    fc = {
        "recovery_hint": "Refined unique search targeting implementation modules and callsites.",
        "recent_searches": ["Search for dispatch and StepExecutor in codebase"],
        "dominant_artifact_mode": "code",
    }
    steps = [
        {"id": 1, "action": "SEARCH", "description": "Search for dispatch and StepExecutor in codebase", "reason": "r"},
        {
            "id": 2,
            "action": "SEARCH",
            "description": "Find dispatch and StepExecutor in the codebase",
            "reason": "r",
        },
        {"id": 3, "action": "EXPLAIN", "description": "Explain connection", "reason": "r"},
    ]
    out, mut = repair_replan_steps_for_recovery(
        steps,
        fc,
        RECOVERY_NEED_IMPLEMENTATION_SEARCH,
    )
    assert mut is True
    search_steps = [s for s in out if (s.get("action") or "").upper() == "SEARCH"]
    assert len(search_steps) <= 2
    assert any("Refined unique" in (s.get("description") or "") for s in out if s.get("action") == "SEARCH")


def test_repair_inserts_search_before_explain_only():
    fc = {
        "recovery_hint": "INSERTED_SEARCH_HINT_XYZ",
        "recent_searches": [],
        "dominant_artifact_mode": "code",
    }
    steps = [{"id": 1, "action": "EXPLAIN", "description": "Explain foo", "reason": "r"}]
    out, mut = repair_replan_steps_for_recovery(
        steps,
        fc,
        RECOVERY_NEED_SEARCH_BEFORE_EXPLAIN,
    )
    assert mut is True
    assert (out[0].get("action") or "").upper() == "SEARCH"
    assert "INSERTED_SEARCH_HINT_XYZ" in (out[0].get("description") or "")


def test_repair_docs_lane_does_not_add_code_jargon():
    fc = {
        "recovery_hint": "Docs hint",
        "recent_searches": [],
        "dominant_artifact_mode": "docs",
    }
    steps = [
        {"id": 1, "action": "SEARCH", "description": "readme", "reason": "r", "artifact_mode": "docs"},
        {"id": 2, "action": "EXPLAIN", "description": "Explain", "reason": "r", "artifact_mode": "docs"},
    ]
    out, _ = repair_replan_steps_for_recovery(steps, fc, RECOVERY_GENERIC_FAILURE)
    for s in out:
        if s.get("artifact_mode"):
            assert s.get("artifact_mode") == "docs"


def test_repair_better_build_context_inserts_search_before_explain():
    fc = {
        "recovery_hint": "SEARCH_FOR_CANDIDATES_ABC",
        "recent_searches": [],
        "dominant_artifact_mode": "code",
    }
    steps = [{"id": 1, "action": "EXPLAIN", "description": "x", "reason": "r"}]
    out, mut = repair_replan_steps_for_recovery(
        steps,
        fc,
        RECOVERY_NEED_BETTER_BUILD_CONTEXT,
    )
    assert mut is True
    assert out[0]["action"] == "SEARCH"


# --- build_replan_failure_context integration ---


def test_build_context_scans_backward_for_search_quality():
    sr_search = StepResult(
        step_id=1,
        action="SEARCH",
        success=True,
        output={"query": "generic query about dispatch", "results": []},
        latency_seconds=0.1,
    )
    sr_explain = StepResult(
        step_id=3,
        action="EXPLAIN",
        success=False,
        output="",
        latency_seconds=0.1,
        error="EXPLAIN failed",
        reason_code="insufficient_substantive_context",
    )
    st = _state(
        "How does dispatch reach StepExecutor",
        steps=[{"id": 1}, {"id": 2}, {"id": 3}],
        step_results=[sr_search, sr_explain],
        context={"search_quality": "weak", "last_dispatch_reason_code": "insufficient_substantive_context"},
    )
    fc = build_replan_failure_context(st, {"id": 3, "action": "EXPLAIN", "description": "Explain dispatch"}, "boom")
    assert fc["recovery_mode"] == RECOVERY_NEED_IMPLEMENTATION_SEARCH
    assert "generic query" in str(fc.get("recent_searches"))


# --- replan integration (mocked LLM) ---


def test_replan_applies_recovery_repair_on_mock_llm_output():
    mock_json = (
        '{"steps": ['
        '{"id": 1, "action": "SEARCH", "description": "Search for dispatch and StepExecutor in codebase", "reason": "r"},'
        '{"id": 2, "action": "SEARCH", "description": "Find dispatch StepExecutor codebase", "reason": "r"},'
        '{"id": 3, "action": "EXPLAIN", "description": "Explain", "reason": "r"}'
        "]}"
    )
    st = _state(
        "dispatch StepExecutor",
        steps=[{"id": 1, "action": "SEARCH"}, {"id": 2, "action": "EXPLAIN"}],
        step_results=[
            StepResult(
                1,
                "SEARCH",
                True,
                {"query": "Search for dispatch and StepExecutor in codebase"},
                0.1,
            ),
        ],
        context={"search_quality": "weak", "last_dispatch_reason_code": None},
    )
    failed = {"id": 2, "action": "EXPLAIN", "description": "Explain", "reason": "r"}
    with (
        patch("agent.orchestrator.replanner.get_model_for_task", return_value=ModelType.REASONING),
        patch("agent.orchestrator.replanner.call_reasoning_model", return_value=mock_json),
    ):
        plan = replan(st, failed_step=failed, error="EXPLAIN received non-substantive context")

    steps = plan.get("steps") or []
    assert len(steps) >= 1
    assert any((s.get("action") or "").upper() == "EXPLAIN" for s in steps)


def test_replan_explain_only_mock_inserts_search():
    mock_json = '{"steps": [{"id": 1, "action": "EXPLAIN", "description": "Only explain", "reason": "r"}]}'
    st = _state("Why foo", steps=[{"id": 1}], step_results=[], context={})
    failed = {"id": 1, "action": "EXPLAIN", "description": "Only explain", "reason": "r"}
    with (
        patch("agent.orchestrator.replanner.get_model_for_task", return_value=ModelType.REASONING),
        patch("agent.orchestrator.replanner.call_reasoning_model", return_value=mock_json),
    ):
        plan = replan(
            st,
            failed_step=failed,
            error="EXPLAIN blocked: insufficient grounding evidence",
        )
    steps = plan.get("steps") or []
    assert (steps[0].get("action") or "").upper() == "SEARCH"
    assert len(steps) >= 2
