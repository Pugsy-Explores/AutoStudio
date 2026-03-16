"""Tests for graph expansion telemetry (graph_nodes_expanded, graph_edges_traversed, graph_expansion_depth_used)."""

from pathlib import Path

import pytest

from agent.retrieval.symbol_expander import expand_from_anchors
from repo_graph.graph_builder import build_graph


def _make_graph(tmp_path, symbols, edges):
    index_dir = tmp_path / ".symbol_graph"
    index_dir.mkdir()
    db_path = index_dir / "index.sqlite"
    build_graph(symbols, edges, str(db_path))


def test_graph_telemetry_emitted(tmp_path):
    """expand_from_anchors populates graph_telemetry_out with graph_nodes_expanded, graph_edges_traversed, graph_expansion_depth_used."""
    symbols = [
        {"symbol_name": "A", "symbol_type": "function", "file": "a.py", "start_line": 1, "end_line": 5},
        {"symbol_name": "B", "symbol_type": "function", "file": "b.py", "start_line": 1, "end_line": 5},
    ]
    edges = [{"source_symbol": "A", "target_symbol": "B", "relation_type": "calls"}]
    _make_graph(tmp_path, symbols, edges)

    telemetry = {}
    result = expand_from_anchors(
        [{"file": "a.py", "symbol": "A", "line": 1}],
        "query",
        project_root=str(tmp_path),
        graph_telemetry_out=telemetry,
    )
    assert "graph_nodes_expanded" in telemetry
    assert "graph_edges_traversed" in telemetry
    assert "graph_expansion_depth_used" in telemetry
    assert telemetry["graph_nodes_expanded"] == len(result) or telemetry["graph_nodes_expanded"] >= 1


def test_graph_telemetry_no_index(tmp_path):
    """When no index, graph_telemetry_out is not populated (expand returns [])."""
    telemetry = {}
    result = expand_from_anchors(
        [{"file": "a.py", "symbol": "A", "line": 1}],
        "query",
        project_root=str(tmp_path),
        graph_telemetry_out=telemetry,
    )
    assert result == []
    assert not telemetry
