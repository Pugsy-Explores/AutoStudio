"""Tests for agent/retrieval/graph_retriever."""

import tempfile
from pathlib import Path

import pytest

from agent.retrieval.graph_retriever import retrieve_symbol_context
from repo_graph.graph_builder import build_graph
from repo_graph.graph_storage import GraphStorage


def test_retrieve_symbol_context_no_index():
    """retrieve_symbol_context returns None when no index."""
    result = retrieve_symbol_context("foo", project_root="/nonexistent/path")
    assert result is None


def test_retrieve_symbol_context_empty_query():
    """retrieve_symbol_context returns empty results for empty query."""
    result = retrieve_symbol_context("", project_root="/tmp")
    assert result is not None
    assert result.get("results") == []
    assert result.get("query") == ""


def test_retrieve_symbol_context_with_index(tmp_path):
    """retrieve_symbol_context returns results when index exists."""
    symbols = [
        {"symbol_name": "dispatch", "symbol_type": "function", "file": "d.py", "start_line": 1, "end_line": 10, "docstring": "Dispatch step"},
        {"symbol_name": "run", "symbol_type": "function", "file": "d.py", "start_line": 11, "end_line": 20, "docstring": "Run"},
    ]
    edges = [{"source_symbol": "dispatch", "target_symbol": "run", "relation_type": "calls"}]
    index_dir = tmp_path / ".symbol_graph"
    index_dir.mkdir()
    db_path = index_dir / "index.sqlite"
    build_graph(symbols, edges, str(db_path))

    result = retrieve_symbol_context("dispatch", project_root=str(tmp_path))
    assert result is not None
    assert "results" in result
    assert len(result["results"]) >= 1
    assert result["results"][0].get("symbol") == "dispatch"
    assert result["results"][0].get("file") == "d.py"
    assert result.get("query") == "dispatch"


def test_retrieve_symbol_context_max_symbols(tmp_path):
    """retrieve_symbol_context caps at MAX_RETRIEVED_SYMBOLS."""
    symbols = [{"symbol_name": f"s{i}", "symbol_type": "function", "file": "f.py", "start_line": i, "end_line": i + 1} for i in range(25)]
    edges = [{"source_symbol": "s0", "target_symbol": f"s{i}", "relation_type": "calls"} for i in range(1, 25)]
    index_dir = tmp_path / ".symbol_graph"
    index_dir.mkdir()
    db_path = index_dir / "index.sqlite"
    build_graph(symbols, edges, str(db_path))

    result = retrieve_symbol_context("s0", project_root=str(tmp_path))
    assert result is not None
    assert len(result["results"]) <= 15
