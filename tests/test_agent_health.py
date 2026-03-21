"""Agent health score tests. Attribution-driven, no fallbacks, no corrections."""

from __future__ import annotations

import pytest

from tests.agent_eval.check_retrieval_quality import aggregate_retrieval_metrics


def _rec(task_id: str, failure_reason: str | None = None) -> dict:
    """Minimal record with failure_reason."""
    r: dict = {"task_id": task_id}
    if failure_reason is not None:
        r["failure_reason"] = failure_reason
    return r


def test_all_success_score_one():
    """All SUCCESS -> overall_score=1.0, weighted_failure=0.0."""
    recs = [_rec("a", "SUCCESS"), _rec("b", "SUCCESS"), _rec("c", "SUCCESS")]
    agg = aggregate_retrieval_metrics(recs)
    h = agg["agent_health"]
    assert h["overall_score"] == pytest.approx(1.0)
    assert h["weighted_failure"] == pytest.approx(0.0)
    assert h["attribution_coverage"] == pytest.approx(1.0)
    assert h["unknown_failure_reasons"] == []


def test_all_planning_loop_score_zero():
    """All PLANNING_LOOP -> overall_score=0.0, weighted_failure=1.0."""
    recs = [_rec("a", "PLANNING_LOOP"), _rec("b", "PLANNING_LOOP")]
    agg = aggregate_retrieval_metrics(recs)
    h = agg["agent_health"]
    assert h["overall_score"] == pytest.approx(0.0)
    assert h["weighted_failure"] == pytest.approx(1.0)
    assert h["attribution_coverage"] == pytest.approx(1.0)
    assert h["unknown_failure_reasons"] == []


def test_unknown_reasons_surfaced_not_used():
    """Unknown failure reasons are surfaced but not used in score."""
    recs = [
        _rec("a", "SUCCESS"),
        _rec("b", "NEW_UNKNOWN"),
    ]
    agg = aggregate_retrieval_metrics(recs)
    h = agg["agent_health"]
    assert h["unknown_failure_reasons"] == ["NEW_UNKNOWN"]
    # Score computed only from valid rows (SUCCESS)
    assert h["overall_score"] == pytest.approx(1.0)
    assert h["weighted_failure"] == pytest.approx(0.0)
    assert h["attribution_coverage"] == pytest.approx(1.0)


def test_missing_reason_reduces_coverage():
    """Rows without failure_reason reduce attribution_coverage."""
    recs = [
        _rec("a", "SUCCESS"),
        _rec("b"),  # no failure_reason
    ]
    agg = aggregate_retrieval_metrics(recs)
    h = agg["agent_health"]
    assert h["attribution_coverage"] == pytest.approx(0.5)
    # Score from valid rows only (1 SUCCESS)
    assert h["overall_score"] == pytest.approx(1.0)
    assert h["unknown_failure_reasons"] == []


def test_score_only_from_valid_rows():
    """Score excludes unknown and missing rows."""
    recs = [
        _rec("a", "SUCCESS"),
        _rec("b", "GROUNDING_FAILURE"),
        _rec("c", "UNKNOWN_FUTURE_REASON"),
        _rec("d"),  # missing
    ]
    agg = aggregate_retrieval_metrics(recs)
    h = agg["agent_health"]
    assert h["unknown_failure_reasons"] == ["UNKNOWN_FUTURE_REASON"]
    assert h["attribution_coverage"] == pytest.approx(0.75)  # 3/4 have reason
    # valid_rows = a(SUCCESS), b(GROUNDING_FAILURE)
    # weighted_failure = (0*0 + 1*0.8) / 2 = 0.4
    # overall_score = 0.6
    assert h["weighted_failure"] == pytest.approx(0.4)
    assert h["overall_score"] == pytest.approx(0.6)


def test_subscores_match_expected_ratios():
    """Each subscore = 1 - (group count / valid_total)."""
    recs = [
        _rec("a", "SUCCESS"),
        _rec("b", "RETRIEVAL_FAILURE"),
        _rec("c", "SELECTION_FAILURE"),
        _rec("d", "GROUNDING_FAILURE"),
    ]
    agg = aggregate_retrieval_metrics(recs)
    h = agg["agent_health"]
    sub = h["subscores"]
    # retrieval: 1 - 1/4 = 0.75 (RETRIEVAL_FAILURE)
    assert sub["retrieval"] == pytest.approx(0.75)
    # selection: 1 - 1/4 = 0.75 (SELECTION_FAILURE)
    assert sub["selection"] == pytest.approx(0.75)
    # exploration: 1 - 0/4 = 1.0
    assert sub["exploration"] == pytest.approx(1.0)
    # grounding: 1 - 1/4 = 0.75 (GROUNDING_FAILURE)
    assert sub["grounding"] == pytest.approx(0.75)
    # planning: 1 - 0/4 = 1.0
    assert sub["planning"] == pytest.approx(1.0)


def test_no_rows_all_none():
    """Empty records -> overall_score, weighted_failure, subscores, attribution_coverage all None."""
    agg = aggregate_retrieval_metrics([])
    h = agg["agent_health"]
    assert h["overall_score"] is None
    assert h["weighted_failure"] is None
    assert h["attribution_coverage"] is None
    assert h["unknown_failure_reasons"] == []
    for k, v in h["subscores"].items():
        assert v is None, f"subscores[{k}] should be None"
