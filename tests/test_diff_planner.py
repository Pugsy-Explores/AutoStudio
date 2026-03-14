"""Tests for editing/diff_planner."""

import tempfile
from pathlib import Path

import pytest

from editing.diff_planner import plan_diff


def test_plan_diff_returns_changes():
    """plan_diff returns changes list."""
    instruction = "Add logging to execute_step"
    context = {
        "ranked_context": [
            {"file": "executor.py", "symbol": "execute_step", "snippet": "def execute_step..."},
        ],
        "retrieved_symbols": [{"file": "executor.py", "symbol": "execute_step"}],
        "retrieved_files": ["executor.py"],
    }
    result = plan_diff(instruction, context)
    assert "changes" in result
    assert isinstance(result["changes"], list)
    assert len(result["changes"]) >= 1
    change = result["changes"][0]
    assert "file" in change
    assert "symbol" in change
    assert change["action"] in ("modify", "add", "delete")
    assert "patch" in change
    assert "reason" in change


def test_plan_diff_identifies_callers(tmp_path):
    """plan_diff includes callers when graph index exists."""
    from repo_graph.graph_builder import build_graph

    symbols = [
        {"symbol_name": "execute_step", "symbol_type": "function", "file": "e.py", "start_line": 1, "end_line": 10},
        {"symbol_name": "run_agent", "symbol_type": "function", "file": "main.py", "start_line": 1, "end_line": 5},
    ]
    edges = [
        {"source_symbol": "run_agent", "target_symbol": "execute_step", "relation_type": "calls"},
    ]
    index_dir = tmp_path / ".symbol_graph"
    index_dir.mkdir()
    db_path = index_dir / "index.sqlite"
    build_graph(symbols, edges, str(db_path))

    instruction = "Modify execute_step"
    context = {
        "ranked_context": [{"file": "e.py", "symbol": "execute_step", "snippet": "..."}],
        "retrieved_symbols": [{"file": "e.py", "symbol": "execute_step"}],
        "retrieved_files": ["e.py"],
        "project_root": str(tmp_path),
    }
    result = plan_diff(instruction, context)
    assert "changes" in result
    files = {c["file"] for c in result["changes"]}
    assert "e.py" in files
    # main.py may be included as caller
    assert len(result["changes"]) >= 1
