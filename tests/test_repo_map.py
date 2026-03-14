"""Tests for repo_graph/repo_map_builder."""

import json
from pathlib import Path

import pytest

from repo_graph.graph_builder import build_graph
from repo_graph.repo_map_builder import build_repo_map


def test_build_repo_map_empty(tmp_path):
    """build_repo_map returns empty modules when no symbols."""
    result = build_repo_map(str(tmp_path))
    assert "modules" in result
    assert result["modules"] == []


def test_build_repo_map_with_symbols(tmp_path):
    """build_repo_map generates modules from symbols.json."""
    index_dir = tmp_path / ".symbol_graph"
    index_dir.mkdir()
    symbols = [
        {"symbol_name": "foo", "symbol_type": "function", "file": str(tmp_path / "a.py"), "start_line": 1, "end_line": 5, "docstring": ""},
        {"symbol_name": "Bar", "symbol_type": "class", "file": str(tmp_path / "a.py"), "start_line": 7, "end_line": 10, "docstring": ""},
        {"symbol_name": "baz", "symbol_type": "function", "file": str(tmp_path / "b.py"), "start_line": 1, "end_line": 3, "docstring": ""},
    ]
    with open(index_dir / "symbols.json", "w") as f:
        json.dump(symbols, f, indent=2)

    result = build_repo_map(str(tmp_path))
    assert "modules" in result
    assert len(result["modules"]) >= 1
    mod_names = [m["name"] for m in result["modules"]]
    assert "a" in mod_names or any("a" in n for n in mod_names)


def test_build_repo_map_with_graph(tmp_path):
    """build_repo_map includes dependencies when index exists."""
    index_dir = tmp_path / ".symbol_graph"
    index_dir.mkdir()
    a_path = str(tmp_path / "a.py")
    b_path = str(tmp_path / "b.py")
    symbols = [
        {"symbol_name": "foo", "symbol_type": "function", "file": a_path, "start_line": 1, "end_line": 5, "docstring": ""},
        {"symbol_name": "bar", "symbol_type": "function", "file": b_path, "start_line": 1, "end_line": 5, "docstring": ""},
    ]
    edges = [{"source_symbol": "foo", "target_symbol": "bar", "relation_type": "calls"}]
    db_path = index_dir / "index.sqlite"
    build_graph(symbols, edges, str(db_path))
    with open(index_dir / "symbols.json", "w") as f:
        json.dump(symbols, f, indent=2)

    result = build_repo_map(str(tmp_path))
    assert "modules" in result
    assert len(result["modules"]) >= 1
    map_path = index_dir / "repo_map.json"
    assert map_path.exists()
    with open(map_path) as f:
        stored = json.load(f)
    assert "modules" in stored
