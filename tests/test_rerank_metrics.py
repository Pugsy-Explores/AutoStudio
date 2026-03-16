"""Tests for reranker impact metrics."""

from unittest.mock import MagicMock

from agent.retrieval.retrieval_pipeline import _compute_rerank_impact, _log_rerank_telemetry


def test_compute_rerank_impact_position_changes():
    before = [
        {"file": "a.py", "symbol": "f1", "snippet": "s1"},
        {"file": "b.py", "symbol": "f2", "snippet": "s2"},
        {"file": "c.py", "symbol": "f3", "snippet": "s3"},
    ]
    # After: reorder so b first, then a, then c
    after = [
        {"file": "b.py", "symbol": "f2", "snippet": "s2"},
        {"file": "a.py", "symbol": "f1", "snippet": "s1"},
        {"file": "c.py", "symbol": "f3", "snippet": "s3"},
    ]
    impact = _compute_rerank_impact(before, after)
    assert impact["rerank_position_changes"] >= 2
    assert impact["rerank_top1_changed"] == 1


def test_compute_rerank_impact_no_change():
    before = [{"file": "a.py", "symbol": "f", "snippet": "s"}]
    after = [{"file": "a.py", "symbol": "f", "snippet": "s"}]
    impact = _compute_rerank_impact(before, after)
    assert impact["rerank_position_changes"] == 0
    assert impact["rerank_top1_changed"] == 0


def test_log_rerank_telemetry_includes_impact():
    state = MagicMock()
    state.context = {}
    _log_rerank_telemetry(
        state, 10, "cpu", 20, 18, 10, 100, None,
        impact={"rerank_position_changes": 5, "rerank_avg_rank_shift": 1.2, "rerank_top1_changed": 1},
    )
    assert state.context["retrieval_metrics"]["rerank_position_changes"] == 5
    assert state.context["retrieval_metrics"]["rerank_avg_rank_shift"] == 1.2
    assert state.context["retrieval_metrics"]["rerank_top1_changed"] == 1
