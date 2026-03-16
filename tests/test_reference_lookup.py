"""Tests for agent/tools/reference_tools.find_referencing_symbols."""

from pathlib import Path

import pytest

from agent.tools.reference_tools import find_referencing_symbols
from repo_graph.graph_builder import build_graph


def _make_graph(tmp_path, symbols, edges):
    index_dir = tmp_path / ".symbol_graph"
    index_dir.mkdir()
    db_path = index_dir / "index.sqlite"
    build_graph(symbols, edges, str(db_path))


def test_find_referencing_symbols_no_symbol_no_path():
    """find_referencing_symbols returns empty dict when both symbol and path empty."""
    result = find_referencing_symbols("", "")
    assert result == {"callers": [], "callees": [], "imports": [], "referenced_by": []}


def test_find_referencing_symbols_no_index(tmp_path):
    """find_referencing_symbols returns empty dict when no graph index."""
    result = find_referencing_symbols("foo", str(tmp_path / "foo.py"), project_root=str(tmp_path))
    assert result == {"callers": [], "callees": [], "imports": [], "referenced_by": []}


def test_find_referencing_symbols_with_graph(tmp_path):
    """find_referencing_symbols returns structured dict when index exists."""
    symbols = [
        {"symbol_name": "dispatch", "symbol_type": "function", "file": "d.py", "start_line": 1, "end_line": 10},
        {"symbol_name": "run", "symbol_type": "function", "file": "r.py", "start_line": 1, "end_line": 10},
    ]
    edges = [
        {"source_symbol": "dispatch", "target_symbol": "run", "relation_type": "calls"},
    ]
    _make_graph(tmp_path, symbols, edges)

    result = find_referencing_symbols("run", "", project_root=str(tmp_path))
    assert "callers" in result
    assert "callees" in result
    assert "imports" in result
    assert "referenced_by" in result
    assert len(result["callers"]) == 1
    assert result["callers"][0].get("symbol") == "dispatch"
    assert result["callers"][0].get("file") == "d.py"
    assert result["callers"][0].get("snippet")


def test_find_referencing_symbols_cap_10(tmp_path):
    """find_referencing_symbols caps each list at 10."""
    symbols = [
        {"symbol_name": "target", "symbol_type": "function", "file": "t.py", "start_line": 1, "end_line": 5},
    ] + [
        {"symbol_name": f"caller{i}", "symbol_type": "function", "file": f"c{i}.py", "start_line": 1, "end_line": 5}
        for i in range(15)
    ]
    edges = [{"source_symbol": f"caller{i}", "target_symbol": "target", "relation_type": "calls"} for i in range(15)]
    _make_graph(tmp_path, symbols, edges)

    result = find_referencing_symbols("target", "", project_root=str(tmp_path))
    assert len(result["callers"]) <= 10


def test_find_referencing_symbols_symbol_not_found(tmp_path):
    """find_referencing_symbols returns empty dict when symbol not in graph."""
    symbols = [
        {"symbol_name": "other", "symbol_type": "function", "file": "o.py", "start_line": 1, "end_line": 5},
    ]
    _make_graph(tmp_path, symbols, [])

    result = find_referencing_symbols("Nonexistent", "", project_root=str(tmp_path))
    assert result == {"callers": [], "callees": [], "imports": [], "referenced_by": []}
