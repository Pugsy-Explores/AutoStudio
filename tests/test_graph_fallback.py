"""Tests for graph index fallback (graph_stage_skipped metric)."""

from pathlib import Path

import pytest

from agent.memory.state import AgentState
from agent.retrieval.retrieval_pipeline import run_retrieval_pipeline


def test_graph_stage_skipped_when_no_index(tmp_path):
    """graph_stage_skipped is True when .symbol_graph/index.sqlite absent."""
    state = AgentState(instruction="test", current_plan={"steps": []})
    state.context["project_root"] = str(tmp_path)
    search_results = [
        {"file": "a.py", "symbol": "foo", "line": 1, "snippet": "def foo(): pass"},
    ]

    result = run_retrieval_pipeline(search_results, state, "foo")

    metrics = state.context.get("retrieval_metrics") or {}
    assert metrics.get("graph_stage_skipped") is True
    assert "results" in result


def test_graph_stage_skipped_pipeline_continues(tmp_path):
    """Pipeline does not crash when index absent; symbol_snippets remains []."""
    state = AgentState(instruction="test", current_plan={"steps": []})
    state.context["project_root"] = str(tmp_path)
    search_results = [
        {"file": "a.py", "symbol": "foo", "line": 1, "snippet": "def foo(): pass"},
    ]

    result = run_retrieval_pipeline(search_results, state, "foo")

    assert "results" in result
    assert "anchors" in result
    assert state.context.get("ranked_context") is not None
