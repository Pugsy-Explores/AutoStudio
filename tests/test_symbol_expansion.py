"""Tests for agent/retrieval/symbol_expander."""

from pathlib import Path
from unittest.mock import patch

import pytest

from agent.retrieval.symbol_expander import (
    MAX_SNIPPETS,
    MAX_SYMBOLS,
    expand_from_anchors,
)
from repo_graph.graph_builder import build_graph


def test_expand_from_anchors_no_index():
    """expand_from_anchors returns [] when no graph index exists."""
    result = expand_from_anchors(
        [{"file": "foo.py", "symbol": "bar", "line": 1}],
        "query",
        project_root="/nonexistent/path",
    )
    assert result == []


def test_expand_from_anchors_empty_anchors():
    """expand_from_anchors returns [] for empty anchors."""
    result = expand_from_anchors([], "query", project_root="/tmp")
    assert result == []


def test_expand_from_anchors_empty_query():
    """expand_from_anchors returns [] for empty query."""
    result = expand_from_anchors(
        [{"file": "foo.py", "symbol": "bar", "line": 1}],
        "",
        project_root="/tmp",
    )
    assert result == []


def test_expand_from_anchors_no_symbol_in_anchors(tmp_path):
    """expand_from_anchors returns [] when anchors have no symbol field."""
    index_dir = tmp_path / ".symbol_graph"
    index_dir.mkdir()
    db_path = index_dir / "index.sqlite"
    build_graph(
        [{"symbol_name": "dispatch", "symbol_type": "function", "file": "d.py", "start_line": 1, "end_line": 10}],
        [],
        str(db_path),
    )
    result = expand_from_anchors(
        [{"file": "d.py", "symbol": "", "line": 1}],
        "dispatch",
        project_root=str(tmp_path),
    )
    assert result == []


def test_expand_from_anchors_with_index(tmp_path):
    """expand_from_anchors returns snippets when index exists and anchor matches."""
    symbols = [
        {"symbol_name": "dispatch", "symbol_type": "function", "file": "d.py", "start_line": 1, "end_line": 10, "docstring": "Dispatch step"},
        {"symbol_name": "run", "symbol_type": "function", "file": "d.py", "start_line": 11, "end_line": 20, "docstring": "Run"},
    ]
    edges = [{"source_symbol": "dispatch", "target_symbol": "run", "relation_type": "calls"}]
    index_dir = tmp_path / ".symbol_graph"
    index_dir.mkdir()
    db_path = index_dir / "index.sqlite"
    build_graph(symbols, edges, str(db_path))

    result = expand_from_anchors(
        [{"file": "d.py", "symbol": "dispatch", "line": 1}],
        "dispatch",
        project_root=str(tmp_path),
    )
    assert len(result) >= 1
    assert len(result) <= MAX_SNIPPETS
    for r in result:
        assert "file" in r
        assert "symbol" in r
        assert "snippet" in r


def test_expand_from_anchors_max_snippets(tmp_path):
    """expand_from_anchors respects max_snippets cap."""
    symbols = [
        {"symbol_name": f"s{i}", "symbol_type": "function", "file": "f.py", "start_line": i, "end_line": i + 1} for i in range(25)
    ]
    edges = [{"source_symbol": "s0", "target_symbol": f"s{i}", "relation_type": "calls"} for i in range(1, 25)]
    index_dir = tmp_path / ".symbol_graph"
    index_dir.mkdir()
    db_path = index_dir / "index.sqlite"
    build_graph(symbols, edges, str(db_path))

    result = expand_from_anchors(
        [{"file": "f.py", "symbol": "s0", "line": 1}],
        "s0",
        project_root=str(tmp_path),
        max_snippets=6,
    )
    assert len(result) <= 6


def test_expand_from_anchors_max_symbols(tmp_path):
    """expand_from_anchors respects max_symbols cap before ranking."""
    symbols = [
        {"symbol_name": f"sym{i}", "symbol_type": "function", "file": "f.py", "start_line": i, "end_line": i + 1} for i in range(20)
    ]
    edges = [{"source_symbol": "sym0", "target_symbol": f"sym{i}", "relation_type": "calls"} for i in range(1, 20)]
    index_dir = tmp_path / ".symbol_graph"
    index_dir.mkdir()
    db_path = index_dir / "index.sqlite"
    build_graph(symbols, edges, str(db_path))

    result = expand_from_anchors(
        [{"file": "f.py", "symbol": "sym0", "line": 1}],
        "sym0",
        project_root=str(tmp_path),
        max_symbols=15,
        max_snippets=6,
    )
    assert len(result) <= 6


def test_expand_from_anchors_anchor_not_in_graph(tmp_path):
    """expand_from_anchors returns [] when anchor symbol not in graph."""
    index_dir = tmp_path / ".symbol_graph"
    index_dir.mkdir()
    db_path = index_dir / "index.sqlite"
    build_graph(
        [{"symbol_name": "dispatch", "symbol_type": "function", "file": "d.py", "start_line": 1, "end_line": 10}],
        [],
        str(db_path),
    )
    result = expand_from_anchors(
        [{"file": "d.py", "symbol": "NonexistentSymbol", "line": 1}],
        "query",
        project_root=str(tmp_path),
    )
    assert result == []


def test_expand_from_anchors_uses_first_matching_anchor(tmp_path):
    """expand_from_anchors uses first anchor with resolvable symbol."""
    symbols = [
        {"symbol_name": "foo", "symbol_type": "function", "file": "a.py", "start_line": 1, "end_line": 5},
        {"symbol_name": "bar", "symbol_type": "function", "file": "b.py", "start_line": 1, "end_line": 5},
    ]
    edges = [{"source_symbol": "foo", "target_symbol": "bar", "relation_type": "calls"}]
    index_dir = tmp_path / ".symbol_graph"
    index_dir.mkdir()
    db_path = index_dir / "index.sqlite"
    build_graph(symbols, edges, str(db_path))

    result = expand_from_anchors(
        [
            {"file": "x.py", "symbol": "Unknown", "line": 1},
            {"file": "a.py", "symbol": "foo", "line": 1},
        ],
        "foo",
        project_root=str(tmp_path),
    )
    assert len(result) >= 1
    assert any(r.get("symbol") == "foo" or r.get("symbol") == "bar" for r in result)
