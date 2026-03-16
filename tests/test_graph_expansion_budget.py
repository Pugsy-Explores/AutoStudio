"""Tests for graph expansion budget (RETRIEVAL_MAX_SYMBOL_EXPANSIONS)."""

from pathlib import Path

import pytest

from repo_graph.graph_builder import build_graph
from repo_graph.graph_query import expand_symbol_dependencies
from repo_graph.graph_storage import GraphStorage


def _make_graph(tmp_path, symbols, edges):
    index_dir = tmp_path / ".symbol_graph"
    index_dir.mkdir()
    db_path = index_dir / "index.sqlite"
    build_graph(symbols, edges, str(db_path))
    return GraphStorage(str(db_path))


def test_expansion_stops_at_max_symbol_expansions(tmp_path):
    """BFS stops when expansions_this_symbol >= max_symbol_expansions."""
    symbols = [
        {"symbol_name": f"s{i}", "symbol_type": "function", "file": "f.py", "start_line": i, "end_line": i + 1}
        for i in range(25)
    ]
    edges = [{"source_symbol": "s0", "target_symbol": f"s{i}", "relation_type": "calls"} for i in range(1, 25)]
    storage = _make_graph(tmp_path, symbols, edges)
    try:
        node = storage.get_symbol_by_name("s0")
        nodes, _ = expand_symbol_dependencies(
            node["id"], storage, depth=10, max_nodes=50, max_symbol_expansions=8
        )
        assert len(nodes) <= 1 + 8
    finally:
        storage.close()


def test_max_nodes_cap_independent(tmp_path):
    """max_nodes cap still fires when max_symbol_expansions is high."""
    symbols = [
        {"symbol_name": f"s{i}", "symbol_type": "function", "file": "f.py", "start_line": i, "end_line": i + 1}
        for i in range(25)
    ]
    edges = [{"source_symbol": "s0", "target_symbol": f"s{i}", "relation_type": "calls"} for i in range(1, 25)]
    storage = _make_graph(tmp_path, symbols, edges)
    try:
        node = storage.get_symbol_by_name("s0")
        nodes, _ = expand_symbol_dependencies(
            node["id"], storage, depth=10, max_nodes=5, max_symbol_expansions=50
        )
        assert len(nodes) <= 5
    finally:
        storage.close()
