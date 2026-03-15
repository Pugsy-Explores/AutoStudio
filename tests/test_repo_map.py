"""Tests for repo_graph/repo_map_builder, repo_map_lookup, anchor_detector, repo_map_updater."""

import json
from pathlib import Path

import pytest

from agent.retrieval.anchor_detector import detect_anchor
from agent.retrieval.repo_map_lookup import lookup_repo_map
from repo_graph.graph_builder import build_graph
from repo_graph.graph_storage import GraphStorage
from repo_graph.repo_map_builder import build_repo_map, build_repo_map_from_storage
from repo_graph.repo_map_updater import update_repo_map_for_file


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
    assert "symbols" in stored
    assert "calls" in stored
    assert len(stored["modules"]) >= 1
    assert len(stored["symbols"]) >= 2


def test_repo_map_build():
    """Build produces modules, symbols, calls in spec format."""
    import tempfile
    with tempfile.TemporaryDirectory() as tmp:
        tmp_path = Path(tmp)
        index_dir = tmp_path / ".symbol_graph"
        index_dir.mkdir()
        a_path = str(tmp_path / "a.py")
        symbols = [
            {"symbol_name": "StepExecutor", "symbol_type": "class", "file": a_path, "start_line": 1, "end_line": 10, "docstring": ""},
            {"symbol_name": "dispatch", "symbol_type": "function", "file": a_path, "start_line": 12, "end_line": 20, "docstring": ""},
        ]
        edges = [{"source_symbol": "StepExecutor", "target_symbol": "dispatch", "relation_type": "calls"}]
        db_path = index_dir / "index.sqlite"
        build_graph(symbols, edges, str(db_path))
        storage = GraphStorage(str(db_path))
        try:
            result = build_repo_map_from_storage(storage, None, tmp_path)
        finally:
            storage.close()
        assert "modules" in result
        assert "symbols" in result
        assert "calls" in result
        assert "StepExecutor" in result["symbols"]
        assert "dispatch" in result["symbols"]
        assert len(result["modules"]) >= 1
        assert len(result["symbols"]) >= 2


def test_repo_map_lookup(tmp_path):
    """lookup_repo_map returns anchor candidates with anchor and file."""
    index_dir = tmp_path / ".symbol_graph"
    index_dir.mkdir()
    repo_map = {
        "modules": {"agent.execution": {"files": ["agent/execution/executor.py"], "symbols": ["StepExecutor", "dispatch"]}},
        "symbols": {
            "StepExecutor": {"file": "agent/execution/executor.py", "type": "class", "line": 15, "module": "agent.execution"},
            "dispatch": {"file": "agent/execution/executor.py", "type": "function", "line": 20, "module": "agent.execution"},
        },
        "calls": {"StepExecutor": ["dispatch"]},
    }
    with open(index_dir / "repo_map.json", "w") as f:
        json.dump(repo_map, f, indent=2)
    candidates = lookup_repo_map("StepExecutor", str(tmp_path))
    assert len(candidates) >= 1
    assert any(c.get("anchor") == "StepExecutor" and "executor" in c.get("file", "") for c in candidates)


def test_anchor_detection():
    """detect_anchor returns {symbol, confidence} for query matching repo_map."""
    repo_map = {
        "symbols": {
            "StepExecutor": {"file": "agent/execution/executor.py", "type": "class", "line": 15, "module": "agent.execution"},
        },
        "modules": {},
        "calls": {},
    }
    anchor = detect_anchor("explain StepExecutor", repo_map)
    assert anchor is not None
    assert anchor.get("symbol") == "StepExecutor"
    assert anchor.get("confidence") in (0.9, 1.0)


def test_repo_map_update_on_edit(tmp_path):
    """After update_index_for_file + update_repo_map_for_file, repo_map reflects changes."""
    from repo_index.indexer import update_index_for_file

    index_dir = tmp_path / ".symbol_graph"
    index_dir.mkdir()
    a_path = tmp_path / "a.py"
    a_path.write_text("def foo(): pass\n")
    symbols = [{"symbol_name": "foo", "symbol_type": "function", "file": str(a_path), "start_line": 1, "end_line": 1, "docstring": ""}]
    with open(index_dir / "symbols.json", "w") as f:
        json.dump(symbols, f, indent=2)
    db_path = index_dir / "index.sqlite"
    build_graph(symbols, [], str(db_path))
    build_repo_map(str(tmp_path))
    with open(index_dir / "repo_map.json") as f:
        before = json.load(f)
    assert "foo" in before.get("symbols", {})

    # Simulate edit: add new symbol
    a_path.write_text("def foo(): pass\ndef bar(): pass\n")
    update_index_for_file(str(a_path), str(tmp_path))
    update_repo_map_for_file(str(a_path), str(tmp_path))

    with open(index_dir / "repo_map.json") as f:
        after = json.load(f)
    assert "bar" in after.get("symbols", {})
