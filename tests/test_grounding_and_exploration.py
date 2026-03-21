"""Tests for grounding and exploration audit (information overlap)."""

from __future__ import annotations

from agent.observability.grounding_audit import _extract_context_tokens, _normalize_tokens
from agent.memory.state import AgentState
from agent.execution.step_dispatcher import _apply_grounding_and_exploration_audit
from tests.agent_eval.check_retrieval_quality import (
    aggregate_retrieval_metrics,
    build_retrieval_quality_record,
)


def test_overlap_based_grounding_works():
    """Sufficient token overlap yields overlap_score > 0.1 and overlap_count > 0."""
    state = AgentState(instruction="test", current_plan={}, context={})
    state.context["ranked_context"] = [
        {"snippet": "def fetch_user_data from api endpoint", "symbol": "fetch_user"},
    ]
    state.context["exploration_debug"] = {"used": False, "exploration_new_tokens": []}
    answer = "The fetch_user_data function retrieves data from the api endpoint."
    _apply_grounding_and_exploration_audit(state, answer)
    g = state.context["grounding_debug"]
    assert g["overlap_score"] > 0.1
    assert g["overlap_count"] > 0


def test_no_overlap_not_grounded():
    """Answer tokens disjoint from context yields overlap_score <= 0.1 or overlap_count == 0."""
    state = AgentState(instruction="test", current_plan={}, context={})
    state.context["ranked_context"] = [
        {"snippet": "xyz abc qwe rty", "symbol": "some_symbol"},
    ]
    state.context["exploration_debug"] = {"used": False, "exploration_new_tokens": []}
    answer = "The system returns a generic response without referencing code."
    _apply_grounding_and_exploration_audit(state, answer)
    g = state.context["grounding_debug"]
    assert g["overlap_score"] <= 0.1 or g["overlap_count"] == 0


def test_exploration_adds_tokens():
    """Exploration adds new tokens when rows gain snippet/symbol content."""
    before = [{"snippet": "foo bar", "symbol": "x"}]
    after = [
        {"snippet": "foo bar", "symbol": "x"},
        {"snippet": "qux zap implementation body", "symbol": "qux"},
    ]
    before_tokens = _extract_context_tokens(before)
    after_tokens = _extract_context_tokens(after)
    new_tokens = after_tokens - before_tokens
    assert len(new_tokens) > 0


def test_exploration_effective_only_when_used_tokens_present():
    """exploration_effective is True only when used_new_token_count > 0."""
    state = AgentState(instruction="test", current_plan={}, context={})
    state.context["ranked_context"] = [{"snippet": "base content", "symbol": "base"}]
    state.context["exploration_debug"] = {
        "used": True,
        "added_count": 1,
        "new_token_count": 3,
        "exploration_new_tokens": ["explored", "token", "here"],
    }
    answer = "The explored token appears in this answer."
    _apply_grounding_and_exploration_audit(state, answer)
    ed = state.context["exploration_debug"]
    assert ed["used_new_token_count"] > 0
    assert ed["exploration_effective"] is True


def test_aggregation_metrics_correct():
    """Aggregate retrieval metrics include avg_overlap_score, exploration_effective_rate, evaluator_coverage_rate."""
    recs = [
        {
            "task_id": "t1",
            "overlap_score": 0.25,
            "exploration_used": True,
            "exploration_effective": True,
            "exploration_used_new_token_count": 2,
            "final_context_tokens": 50,
            "answer_supported": True,
            "final_has_signal": True,
        },
        {
            "task_id": "t2",
            "overlap_score": 0.05,
            "exploration_used": False,
            "exploration_effective": False,
            "exploration_used_new_token_count": 0,
            "final_context_tokens": 30,
            "answer_supported": False,
            "final_has_signal": True,
        },
    ]
    agg = aggregate_retrieval_metrics(recs)
    assert "avg_overlap_score" in agg
    assert agg["avg_overlap_score"] == 0.15
    assert "exploration_effective_rate" in agg
    assert agg["exploration_effective_rate"] == 1.0
    assert "evaluator_coverage_rate" in agg
    assert agg["evaluator_coverage_rate"] == 1.0
    assert "unsupported_with_signal_rate" in agg
    assert agg["unsupported_with_signal_rate"] == 0.5  # 1 of 2


def test_build_retrieval_quality_record_includes_grounding_metrics():
    """build_retrieval_quality_record includes overlap_score, final_context_tokens, grounding_status, final_has_signal."""
    class _Spec:
        task_id = "sq_test"
        tags = ("retrieval_quality",)
        instruction = "test"

    class _State:
        context = {
            "ranked_context": [
                {"snippet": "def example", "symbol": "example", "implementation_body_present": True},
            ],
            "grounding_debug": {
                "overlap_score": 0.2,
                "overlap_count": 5,
            },
            "exploration_debug": {
                "used": False,
                "new_token_count": 0,
                "used_new_token_count": 0,
                "exploration_effective": False,
            },
            "answer_grounding_eval": {"supported": True, "support_strength": 2},
        }
        step_results = []

    rec = build_retrieval_quality_record(_Spec(), _State(), None)
    assert rec["overlap_score"] == 0.2
    assert rec["overlap_count"] == 5
    assert rec["final_context_tokens"] is not None
    assert rec["grounding_status"] == "supported"
    assert rec["final_has_signal"] is True


def test_normalize_tokens_filters_short():
    """Tokens shorter than 3 chars are dropped."""
    tokens = _normalize_tokens("ab cd efgh ij klm")
    assert "ab" not in tokens
    assert "cd" not in tokens
    assert "efgh" in tokens
    assert "klm" in tokens
