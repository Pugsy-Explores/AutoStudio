"""Tests for repo_graph/graph_query: get_callers, get_callees, get_imports, get_referenced_by, expand_symbol_dependencies."""

import tempfile
from pathlib import Path

import pytest

from repo_graph.graph_builder import build_graph
from repo_graph.graph_query import (
    expand_symbol_dependencies,
    get_callees,
    get_callers,
    get_imports,
    get_referenced_by,
)
from repo_graph.graph_storage import GraphStorage


def _make_graph(tmp_path, symbols, edges):
    index_dir = tmp_path / ".symbol_graph"
    index_dir.mkdir()
    db_path = index_dir / "index.sqlite"
    build_graph(symbols, edges, str(db_path))
    return GraphStorage(str(db_path))


def test_get_callers(tmp_path):
    """get_callers returns nodes that call this symbol."""
    symbols = [
        {"symbol_name": "A", "symbol_type": "function", "file": "a.py", "start_line": 1, "end_line": 5},
        {"symbol_name": "B", "symbol_type": "function", "file": "b.py", "start_line": 1, "end_line": 5},
    ]
    edges = [{"source_symbol": "B", "target_symbol": "A", "relation_type": "calls"}]
    storage = _make_graph(tmp_path, symbols, edges)
    try:
        node_a = storage.get_symbol_by_name("A")
        assert node_a
        callers = get_callers(node_a["id"], storage)
        assert len(callers) == 1
        assert callers[0].get("name") == "B"
    finally:
        storage.close()


def test_get_callees(tmp_path):
    """get_callees returns nodes this symbol calls."""
    symbols = [
        {"symbol_name": "A", "symbol_type": "function", "file": "a.py", "start_line": 1, "end_line": 5},
        {"symbol_name": "B", "symbol_type": "function", "file": "b.py", "start_line": 1, "end_line": 5},
    ]
    edges = [{"source_symbol": "A", "target_symbol": "B", "relation_type": "calls"}]
    storage = _make_graph(tmp_path, symbols, edges)
    try:
        node_a = storage.get_symbol_by_name("A")
        assert node_a
        callees = get_callees(node_a["id"], storage)
        assert len(callees) == 1
        assert callees[0].get("name") == "B"
    finally:
        storage.close()


def test_get_imports(tmp_path):
    """get_imports returns nodes this symbol imports."""
    symbols = [
        {"symbol_name": "main", "symbol_type": "module", "file": "main.py", "start_line": 1, "end_line": 5},
        {"symbol_name": "utils", "symbol_type": "module", "file": "utils.py", "start_line": 1, "end_line": 5},
    ]
    edges = [{"source_symbol": "main", "target_symbol": "utils", "relation_type": "imports"}]
    storage = _make_graph(tmp_path, symbols, edges)
    try:
        node = storage.get_symbol_by_name("main")
        assert node
        imports = get_imports(node["id"], storage)
        assert len(imports) == 1
        assert imports[0].get("name") == "utils"
    finally:
        storage.close()


def test_get_referenced_by(tmp_path):
    """get_referenced_by returns nodes that reference this symbol."""
    symbols = [
        {"symbol_name": "Config", "symbol_type": "class", "file": "config.py", "start_line": 1, "end_line": 10},
        {"symbol_name": "app", "symbol_type": "function", "file": "app.py", "start_line": 1, "end_line": 5},
    ]
    edges = [{"source_symbol": "app", "target_symbol": "Config", "relation_type": "references"}]
    storage = _make_graph(tmp_path, symbols, edges)
    try:
        node = storage.get_symbol_by_name("Config")
        assert node
        ref_by = get_referenced_by(node["id"], storage)
        assert len(ref_by) == 1
        assert ref_by[0].get("name") == "app"
    finally:
        storage.close()


def test_expand_symbol_dependencies_no_storage():
    """expand_symbol_dependencies returns ([], telemetry) when storage is None."""
    nodes, telemetry = expand_symbol_dependencies(1, None, depth=2, max_nodes=20)
    assert nodes == []
    assert telemetry["graph_nodes_expanded"] == 0
    assert telemetry["graph_edges_traversed"] == 0


def test_expand_symbol_dependencies_max_nodes(tmp_path):
    """expand_symbol_dependencies respects max_nodes cap."""
    symbols = [
        {"symbol_name": f"s{i}", "symbol_type": "function", "file": "f.py", "start_line": i, "end_line": i + 1}
        for i in range(25)
    ]
    edges = [{"source_symbol": "s0", "target_symbol": f"s{i}", "relation_type": "calls"} for i in range(1, 25)]
    storage = _make_graph(tmp_path, symbols, edges)
    try:
        node = storage.get_symbol_by_name("s0")
        nodes, telemetry = expand_symbol_dependencies(
            node["id"], storage, depth=5, max_nodes=10, max_symbol_expansions=50
        )
        assert len(nodes) <= 10
        assert telemetry["graph_nodes_expanded"] <= 10
    finally:
        storage.close()


def test_expand_symbol_dependencies_max_symbol_expansions(tmp_path):
    """expand_symbol_dependencies respects max_symbol_expansions cap."""
    symbols = [
        {"symbol_name": f"s{i}", "symbol_type": "function", "file": "f.py", "start_line": i, "end_line": i + 1}
        for i in range(25)
    ]
    edges = [{"source_symbol": "s0", "target_symbol": f"s{i}", "relation_type": "calls"} for i in range(1, 25)]
    storage = _make_graph(tmp_path, symbols, edges)
    try:
        node = storage.get_symbol_by_name("s0")
        nodes, telemetry = expand_symbol_dependencies(
            node["id"], storage, depth=5, max_nodes=50, max_symbol_expansions=5
        )
        assert len(nodes) <= 1 + 5
        assert telemetry["graph_nodes_expanded"] <= 6
    finally:
        storage.close()


def test_expand_symbol_dependencies_no_cycles(tmp_path):
    """BFS expansion does not produce cycles (visited set)."""
    symbols = [
        {"symbol_name": "A", "symbol_type": "function", "file": "a.py", "start_line": 1, "end_line": 5},
        {"symbol_name": "B", "symbol_type": "function", "file": "b.py", "start_line": 1, "end_line": 5},
    ]
    edges = [
        {"source_symbol": "A", "target_symbol": "B", "relation_type": "calls"},
        {"source_symbol": "B", "target_symbol": "A", "relation_type": "calls"},
    ]
    storage = _make_graph(tmp_path, symbols, edges)
    try:
        node = storage.get_symbol_by_name("A")
        nodes, telemetry = expand_symbol_dependencies(
            node["id"], storage, depth=5, max_nodes=20, max_symbol_expansions=20
        )
        ids = [n["id"] for n in nodes]
        assert len(ids) == len(set(ids))
    finally:
        storage.close()


def test_expand_symbol_dependencies_telemetry(tmp_path):
    """expand_symbol_dependencies returns telemetry with graph_nodes_expanded, graph_edges_traversed, graph_expansion_depth_used."""
    symbols = [
        {"symbol_name": "A", "symbol_type": "function", "file": "a.py", "start_line": 1, "end_line": 5},
        {"symbol_name": "B", "symbol_type": "function", "file": "b.py", "start_line": 1, "end_line": 5},
    ]
    edges = [{"source_symbol": "A", "target_symbol": "B", "relation_type": "calls"}]
    storage = _make_graph(tmp_path, symbols, edges)
    try:
        node = storage.get_symbol_by_name("A")
        nodes, telemetry = expand_symbol_dependencies(
            node["id"], storage, depth=2, max_nodes=20, max_symbol_expansions=20
        )
        assert "graph_nodes_expanded" in telemetry
        assert "graph_edges_traversed" in telemetry
        assert "graph_expansion_depth_used" in telemetry
        assert telemetry["graph_nodes_expanded"] == len(nodes)
    finally:
        storage.close()
