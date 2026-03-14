"""Tests for repo_graph: graph_storage, graph_builder, graph_query."""

import tempfile
from pathlib import Path

import pytest

from repo_graph.graph_builder import build_graph
from repo_graph.graph_query import expand_neighbors, find_symbol
from repo_graph.graph_storage import GraphStorage


def test_graph_storage_add_node_and_edge(tmp_path):
    """GraphStorage add_node and add_edge work."""
    db = tmp_path / "test.sqlite"
    storage = GraphStorage(str(db))
    n1 = storage.add_node({"symbol_name": "foo", "symbol_type": "function", "file": "a.py", "start_line": 1, "end_line": 5})
    n2 = storage.add_node({"symbol_name": "bar", "symbol_type": "function", "file": "a.py", "start_line": 10, "end_line": 15})
    storage.add_edge(n1, n2, "calls")
    assert n1 > 0
    assert n2 > 0
    node = storage.get_symbol(n1)
    assert node["name"] == "foo"
    neighbors = storage.get_neighbors(n1, direction="out")
    assert len(neighbors) == 1
    assert neighbors[0]["name"] == "bar"
    storage.close()


def test_graph_builder_creates_nodes_and_edges(tmp_path):
    """build_graph creates nodes and edges from symbols and edges."""
    symbols = [
        {"symbol_name": "execute_step", "symbol_type": "function", "file": "e.py", "start_line": 1, "end_line": 10},
        {"symbol_name": "dispatch", "symbol_type": "function", "file": "d.py", "start_line": 1, "end_line": 5},
    ]
    edges = [
        {"source_symbol": "execute_step", "target_symbol": "dispatch", "relation_type": "calls"},
    ]
    db = tmp_path / "graph.sqlite"
    build_graph(symbols, edges, str(db))
    storage = GraphStorage(str(db))
    node = find_symbol("execute_step", storage)
    assert node is not None
    assert node["name"] == "execute_step"
    expanded = expand_neighbors(node["id"], depth=2, storage=storage)
    assert len(expanded) >= 1
    storage.close()


def test_find_symbol_exact_and_like(tmp_path):
    """find_symbol finds by exact name and substring."""
    storage = GraphStorage(str(tmp_path / "g.sqlite"))
    storage.add_node({"symbol_name": "my_function", "symbol_type": "function", "file": "x.py", "start_line": 1, "end_line": 2})
    storage.add_node({"symbol_name": "other", "symbol_type": "function", "file": "x.py", "start_line": 3, "end_line": 4})
    exact = find_symbol("my_function", storage)
    assert exact is not None
    assert exact["name"] == "my_function"
    like = storage.get_symbols_like("func", limit=5)
    assert len(like) >= 1
    assert any("my_function" in n["name"] for n in like)
    storage.close()


def test_expand_neighbors_depth(tmp_path):
    """expand_neighbors respects depth limit."""
    storage = GraphStorage(str(tmp_path / "g.sqlite"))
    n1 = storage.add_node({"symbol_name": "a", "symbol_type": "function", "file": "f.py", "start_line": 1, "end_line": 2})
    n2 = storage.add_node({"symbol_name": "b", "symbol_type": "function", "file": "f.py", "start_line": 3, "end_line": 4})
    n3 = storage.add_node({"symbol_name": "c", "symbol_type": "function", "file": "f.py", "start_line": 5, "end_line": 6})
    storage.add_edge(n1, n2, "calls")
    storage.add_edge(n2, n3, "calls")
    expanded = expand_neighbors(n1, depth=1, storage=storage)
    assert len(expanded) <= 2
    expanded2 = expand_neighbors(n1, depth=2, storage=storage)
    assert len(expanded2) >= 2
    storage.close()
