"""Unit tests for answer grounding evaluation (post-EXPLAIN observability layer)."""

from __future__ import annotations

from unittest.mock import patch

import pytest

from agent.execution.step_dispatcher import _run_answer_grounding_evaluation
from agent.memory.state import AgentState
from tests.agent_eval.check_retrieval_quality import (
    aggregate_retrieval_metrics,
    build_retrieval_quality_record,
)


def _make_state(
    *,
    instruction: str = "explain foo",
    ranked_context: list | None = None,
    bundle_selector_selected_pool: list | None = None,
) -> AgentState:
    ctx: dict = {}
    if ranked_context is not None:
        ctx["ranked_context"] = ranked_context
    if bundle_selector_selected_pool is not None:
        ctx["bundle_selector_selected_pool"] = bundle_selector_selected_pool
    return AgentState(
        instruction=instruction,
        current_plan={"plan_id": "p1", "steps": []},
        context=ctx,
    )


@patch("agent.execution.step_dispatcher.call_small_model")
def test_evaluator_returns_structured_json(mock_call):
    """Evaluator returns structured JSON with supported, support_strength, missing_evidence, context_row_count."""
    mock_call.return_value = '{"supported": true, "support_strength": 3, "missing_evidence": "", "notes": "ok"}'
    state = _make_state(
        ranked_context=[{"snippet": "def foo(): pass"}, {"snippet": "bar"}],
    )
    _run_answer_grounding_evaluation(state, "The foo function exists.")
    eval_res = state.context.get("answer_grounding_eval")
    assert eval_res is not None
    assert eval_res.get("supported") is True
    assert eval_res.get("support_strength") == 3
    assert "missing_evidence" in eval_res
    assert eval_res.get("context_row_count") == 2


@patch("agent.execution.step_dispatcher.call_small_model")
def test_supported_true_when_answer_in_context(mock_call):
    """supported=True when model returns supported."""
    mock_call.return_value = '{"supported": true, "support_strength": 2, "missing_evidence": "", "notes": ""}'
    state = _make_state(ranked_context=[{"snippet": "def bar(): return 1"}])
    _run_answer_grounding_evaluation(state, "bar returns 1.")
    assert state.context["answer_grounding_eval"]["supported"] is True


@patch("agent.execution.step_dispatcher.call_small_model")
def test_supported_false_when_unrelated(mock_call):
    """supported=False when answer not in context."""
    mock_call.return_value = '{"supported": false, "support_strength": 0, "missing_evidence": "no evidence", "notes": ""}'
    state = _make_state(ranked_context=[{"snippet": "unrelated code"}])
    _run_answer_grounding_evaluation(state, "The moon is made of cheese.")
    assert state.context["answer_grounding_eval"]["supported"] is False


@patch("agent.execution.step_dispatcher.call_small_model")
def test_parsing_failure_handled_gracefully(mock_call):
    """Parsing failure sets error and supported=None, no exception."""
    mock_call.return_value = "not valid json at all {"
    state = _make_state(ranked_context=[{"snippet": "x"}])
    _run_answer_grounding_evaluation(state, "answer")
    eval_res = state.context.get("answer_grounding_eval")
    assert eval_res is not None
    assert "error" in eval_res
    assert eval_res.get("supported") is None


def test_build_retrieval_quality_record_includes_fields():
    """build_retrieval_quality_record includes answer_supported and support_strength."""
    class _Spec:
        task_id = "t1"
        tags = ()
        instruction = "test"

    class _State:
        context = {
            "ranked_context": [{"snippet": "x"}],
            "answer_grounding_eval": {
                "supported": True,
                "support_strength": 2,
                "missing_evidence": "",
            },
        }
        step_results = []

    rec = build_retrieval_quality_record(_Spec(), _State(), None)
    assert rec.get("answer_supported") is True
    assert rec.get("support_strength") == 2
    assert rec.get("grounding_status") == "supported"


def test_grounding_status_variants():
    """grounding_status is supported, unsupported, or unknown based on answer_grounding_eval."""
    class _Spec:
        task_id = "t1"
        tags = ()
        instruction = "test"

    def _state(supported):
        class _State:
            context = {"ranked_context": [{}], "answer_grounding_eval": {"supported": supported}}
            step_results = []
        return _State()

    assert build_retrieval_quality_record(_Spec(), _state(True), None)["grounding_status"] == "supported"
    assert build_retrieval_quality_record(_Spec(), _state(False), None)["grounding_status"] == "unsupported"
    assert build_retrieval_quality_record(_Spec(), _state(None), None)["grounding_status"] == "unknown"


def test_aggregation_computes_rates_correctly():
    """Aggregation computes supported_answer_rate, average_support_strength, evaluator_coverage_rate."""
    records = [
        {"answer_supported": True, "support_strength": 3, "final_has_signal": True},
        {"answer_supported": False, "support_strength": 0, "final_has_signal": True},
        {"answer_supported": True, "support_strength": 2, "final_has_signal": False},
        {"answer_supported": None, "support_strength": None, "final_has_signal": True},  # skipped/sampled out
    ]
    agg = aggregate_retrieval_metrics(records)
    assert agg["supported_answer_rate"] == pytest.approx(2 / 3)  # 2 True of 3 with value
    assert agg["average_support_strength"] == pytest.approx(5 / 3)  # (3+0+2)/3
    assert agg["evaluator_coverage_rate"] == 0.75  # 3 of 4 have answer_supported not None
    assert agg["unsupported_with_signal_rate"] == pytest.approx(1 / 4)  # 1 unsupported with signal


@patch("agent.execution.step_dispatcher.ENABLE_ANSWER_EVAL", False)
@patch("agent.execution.step_dispatcher.call_small_model")
def test_sampling_skip_when_disabled(mock_call):
    """When ENABLE_ANSWER_EVAL=False, call_small_model is not invoked."""
    state = _make_state(ranked_context=[{"snippet": "x"}])
    _run_answer_grounding_evaluation(state, "answer")
    mock_call.assert_not_called()
    assert "answer_grounding_eval" not in state.context


@patch("agent.execution.step_dispatcher.call_small_model")
def test_evidence_focused_uses_bundle_selector_pool(mock_call):
    """When bundle_selector_selected_pool exists, it is used (context_row_count reflects selected rows)."""
    mock_call.return_value = '{"supported": true, "support_strength": 2, "missing_evidence": "", "notes": ""}'
    selected = [
        {"snippet": "selected_a" * 50},
        {"snippet": "selected_b" * 50},
    ]
    ranked = [{"snippet": "ranked_x" * 50}] * 10
    state = _make_state(
        ranked_context=ranked,
        bundle_selector_selected_pool=selected,
    )
    _run_answer_grounding_evaluation(state, "answer")
    eval_res = state.context.get("answer_grounding_eval")
    assert eval_res is not None
    assert eval_res.get("context_row_count") == 2  # rows_for_eval = selected[:6] = 2 rows
