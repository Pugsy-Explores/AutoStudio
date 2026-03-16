"""Tests for agent/retrieval/context_builder.build_call_chain_context."""

from pathlib import Path

import pytest

from agent.retrieval.context_builder import build_call_chain_context, build_context_from_symbols
from repo_graph.graph_builder import build_graph


def _make_graph(tmp_path, symbols, edges):
    index_dir = tmp_path / ".symbol_graph"
    index_dir.mkdir()
    db_path = index_dir / "index.sqlite"
    build_graph(symbols, edges, str(db_path))


def test_build_call_chain_context_no_index(tmp_path):
    """build_call_chain_context returns empty dict when no graph index."""
    result = build_call_chain_context("foo", str(tmp_path))
    assert result["symbol"] == "foo"
    assert result["call_chain"] == []
    assert result["dependencies"] == []
    assert result["references"] == []


def test_build_call_chain_context_symbol_not_found(tmp_path):
    """build_call_chain_context returns empty dict when symbol not in graph."""
    symbols = [
        {"symbol_name": "other", "symbol_type": "function", "file": "o.py", "start_line": 1, "end_line": 5},
    ]
    _make_graph(tmp_path, symbols, [])

    result = build_call_chain_context("Nonexistent", str(tmp_path))
    assert result["symbol"] == "Nonexistent"
    assert result["call_chain"] == []
    assert result["dependencies"] == []
    assert result["references"] == []


def test_build_call_chain_context_with_chain(tmp_path):
    """build_call_chain_context returns formatted call chain when path exists."""
    symbols = [
        {"symbol_name": "dispatch", "symbol_type": "function", "file": "d.py", "start_line": 1, "end_line": 10},
        {"symbol_name": "run", "symbol_type": "function", "file": "r.py", "start_line": 11, "end_line": 20},
    ]
    edges = [{"source_symbol": "dispatch", "target_symbol": "run", "relation_type": "calls"}]
    _make_graph(tmp_path, symbols, edges)

    result = build_call_chain_context("dispatch", str(tmp_path))
    assert result["symbol"] == "dispatch"
    assert "call_chain" in result
    assert "dependencies" in result
    assert "references" in result
    assert isinstance(result["dependencies"], list)
    assert isinstance(result["references"], list)


def test_build_context_from_symbols_injects_call_chain(tmp_path):
    """build_context_from_symbols attaches call_chain when project_root and symbols."""
    symbols = [
        {"symbol_name": "dispatch", "symbol_type": "function", "file": "d.py", "start_line": 1, "end_line": 10},
        {"symbol_name": "run", "symbol_type": "function", "file": "r.py", "start_line": 11, "end_line": 20},
    ]
    edges = [{"source_symbol": "dispatch", "target_symbol": "run", "relation_type": "calls"}]
    _make_graph(tmp_path, symbols, edges)

    symbol_results = [{"file": "d.py", "symbol": "dispatch", "snippet": "def dispatch(): pass"}]
    built = build_context_from_symbols(
        symbol_results, [], [], project_root=str(tmp_path)
    )
    assert "call_chain" in built
    assert built["call_chain"]["symbol"] == "dispatch"


def test_build_context_from_symbols_no_call_chain_without_project_root(tmp_path):
    """build_context_from_symbols does not add call_chain when project_root omitted."""
    symbol_results = [{"file": "d.py", "symbol": "dispatch", "snippet": "def dispatch(): pass"}]
    built = build_context_from_symbols(symbol_results, [], [])
    assert "call_chain" not in built


def test_build_context_from_symbols_no_call_chain_without_symbols(tmp_path):
    """build_context_from_symbols does not add call_chain when no symbols."""
    built = build_context_from_symbols([], [], [], project_root=str(tmp_path))
    assert "call_chain" not in built

