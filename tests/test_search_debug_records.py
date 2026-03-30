"""Tests for stage-wise SEARCH audit layer (search_debug_records)."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

from agent.memory.state import AgentState
from agent.retrieval.retrieval_pipeline import (
    _build_search_debug_record,
    run_retrieval_pipeline,
)


def test_records_exist_after_search():
    """Records exist after SEARCH execution."""
    state = AgentState(instruction="test", current_plan={}, context={"project_root": "/tmp"})
    raw = [{"file": "/tmp/a.py", "symbol": "foo", "snippet": "def foo"}]
    with patch("agent.retrieval.retrieval_pipeline.filter_and_rank_search_results", return_value=raw):
        with patch("agent.retrieval.retrieval_pipeline.detect_anchors", return_value=raw[:1]):
            with patch("agent.retrieval.retrieval_pipeline.expand_from_anchors", return_value=[]):
                with patch("agent.retrieval.retrieval_pipeline.expand_search_results", return_value=[]):
                    with patch("agent.retrieval.retrieval_pipeline.build_context_from_symbols", return_value={"symbols": [], "references": [], "files": [], "snippets": []}):
                        with patch("agent.retrieval.retrieval_pipeline._attach_relationship_links", side_effect=lambda x, *_: x):
                            with patch("agent.retrieval.retrieval_pipeline.deduplicate_candidates", side_effect=lambda x: x):
                                with patch("agent.retrieval.retrieval_pipeline.create_reranker", return_value=None):
                                    run_retrieval_pipeline(raw, state, query="find foo")
    records = state.context.get("search_debug_records") or []
    assert len(records) >= 1
    r = records[-1]
    assert "query" in r
    assert "retrieved_count" in r
    assert "candidate_pool_count" in r
    assert "final_count" in r


def test_retrieval_empty_correctly_set():
    """retrieval_empty is True when retrieved_count == 0."""
    state = AgentState(instruction="test", current_plan={}, context={})
    rec = _build_search_debug_record(state, "q", [], [], [])
    assert rec["retrieval_empty"] is True
    assert rec["retrieved_count"] == 0

    rec2 = _build_search_debug_record(
        state, "q", [{"file": "a.py"}], [], []
    )
    assert rec2["retrieval_empty"] is False
    assert rec2["retrieved_count"] == 1


def test_selection_loss_correctly_set():
    """selection_loss is True when pool has signal but final does not."""
    state = AgentState(instruction="test", current_plan={}, context={})
    pool = [{"file": "a.py", "implementation_body_present": True}]
    final = [{"file": "a.py"}]  # no impl/linked in final
    rec = _build_search_debug_record(state, "q", [{"file": "a.py"}], pool, final)
    assert rec["pool_has_signal"] is True
    assert rec["final_has_signal"] is False
    assert rec["selection_loss"] is True


def test_pool_has_signal_computed_correctly():
    """pool_has_signal = has_impl_in_pool or has_linked_in_pool."""
    state = AgentState(instruction="test", current_plan={}, context={})
    rec = _build_search_debug_record(state, "q", [], [], [])
    assert rec["pool_has_signal"] is False
    assert rec["has_impl_in_pool"] is False
    assert rec["has_linked_in_pool"] is False

    pool_impl = [{"file": "a.py", "implementation_body_present": True}]
    rec2 = _build_search_debug_record(state, "q", [{}], pool_impl, pool_impl)
    assert rec2["has_impl_in_pool"] is True
    assert rec2["pool_has_signal"] is True

    pool_linked = [{"file": "a.py", "relations": [{"kind": "import"}]}]
    rec3 = _build_search_debug_record(state, "q", [{}], pool_linked, pool_linked)
    assert rec3["has_linked_in_pool"] is True
    assert rec3["pool_has_signal"] is True


def test_records_survive_full_pipeline_execution():
    """Records survive full pipeline execution and are in state.context."""
    state = AgentState(instruction="test", current_plan={}, context={"project_root": "/tmp"})
    state.context.setdefault("search_debug_records", []).append(
        {"query": "x", "retrieved_count": 1, "retrieval_empty": False, "selection_loss": False}
    )
    raw = [{"file": "/tmp/a.py", "symbol": "foo", "snippet": "x"}]
    with patch("agent.retrieval.retrieval_pipeline.filter_and_rank_search_results", return_value=raw):
        with patch("agent.retrieval.retrieval_pipeline.detect_anchors", return_value=raw[:1]):
            with patch("agent.retrieval.retrieval_pipeline.expand_from_anchors", return_value=[]):
                with patch("agent.retrieval.retrieval_pipeline.expand_search_results", return_value=[]):
                    with patch("agent.retrieval.retrieval_pipeline.build_context_from_symbols", return_value={"symbols": [], "references": [], "files": [], "snippets": []}):
                        with patch("agent.retrieval.retrieval_pipeline._attach_relationship_links", side_effect=lambda x, *_: x):
                            with patch("agent.retrieval.retrieval_pipeline.deduplicate_candidates", side_effect=lambda x: x):
                                with patch("agent.retrieval.retrieval_pipeline.create_reranker", return_value=None):
                                    run_retrieval_pipeline(raw, state, query="find foo")
    records = state.context.get("search_debug_records") or []
    assert len(records) >= 2  # at least the one we added + one from this run
    for r in records:
        assert isinstance(r, dict)
        assert "retrieval_empty" in r
        assert "selection_loss" in r
    # Pipeline-produced records have full schema
    pipeline_records = [r for r in records if "pool_has_signal" in r]
    assert len(pipeline_records) >= 1
