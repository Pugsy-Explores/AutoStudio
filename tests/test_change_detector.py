"""Tests for repo_graph/change_detector."""

from pathlib import Path

import pytest

from repo_graph.change_detector import RISK_HIGH, RISK_LOW, RISK_MEDIUM, detect_change_impact
from repo_graph.graph_builder import build_graph


def test_detect_change_impact_no_index(tmp_path):
    """detect_change_impact works without index."""
    result = detect_change_impact(
        [("a.py", "foo")],
        project_root=str(tmp_path),
    )
    assert "affected_files" in result
    assert "affected_symbols" in result
    assert "risk_level" in result
    assert result["risk_level"] in (RISK_LOW, RISK_MEDIUM)
    assert "a.py" in result["affected_files"]


def test_detect_change_impact_with_index(tmp_path):
    """detect_change_impact finds callers when index exists."""
    index_dir = tmp_path / ".symbol_graph"
    index_dir.mkdir()
    a_path = str(tmp_path / "a.py")
    b_path = str(tmp_path / "b.py")
    symbols = [
        {"symbol_name": "foo", "symbol_type": "function", "file": a_path, "start_line": 1, "end_line": 5, "docstring": ""},
        {"symbol_name": "bar", "symbol_type": "function", "file": b_path, "start_line": 1, "end_line": 5, "docstring": ""},
    ]
    edges = [{"source_symbol": "bar", "target_symbol": "foo", "relation_type": "calls"}]
    db_path = index_dir / "index.sqlite"
    build_graph(symbols, edges, str(db_path))

    result = detect_change_impact([(a_path, "foo")], project_root=str(tmp_path))
    assert "affected_files" in result
    assert "risk_level" in result
    assert b_path in result["affected_files"] or any(b_path in f for f in result["affected_files"])
