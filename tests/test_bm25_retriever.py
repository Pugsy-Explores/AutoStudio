"""Tests for BM25 retriever."""

import pytest


def test_build_index_from_repo_map(tmp_path, monkeypatch):
    """BM25 index builds from repo_map when graph absent."""
    import json
    from pathlib import Path

    from agent.retrieval.bm25_retriever import _reset_for_testing

    _reset_for_testing()
    (tmp_path / ".symbol_graph").mkdir()
    repo_map = {
        "symbols": {
            "StepExecutor": {"file": "agent/execution/executor.py"},
            "DiffPlanner": {"file": "editing/diff_planner.py"},
        }
    }
    (tmp_path / ".symbol_graph" / "repo_map.json").write_text(json.dumps(repo_map))

    monkeypatch.setenv("SERENA_PROJECT_DIR", str(tmp_path))
    from agent.retrieval.bm25_retriever import build_bm25_index, search_bm25

    ok = build_bm25_index(str(tmp_path))
    assert ok is True

    results = search_bm25("StepExecutor", str(tmp_path), top_k=5)
    assert len(results) >= 1
    assert any(r.get("symbol") == "StepExecutor" for r in results)


def test_search_empty_query_returns_empty():
    from agent.retrieval.bm25_retriever import search_bm25

    assert search_bm25("", top_k=10) == []
    assert search_bm25("   ", top_k=10) == []


def test_search_without_index_returns_empty(tmp_path, monkeypatch):
    from agent.retrieval.bm25_retriever import _reset_for_testing, search_bm25

    _reset_for_testing()
    monkeypatch.setenv("SERENA_PROJECT_DIR", str(tmp_path))

    # No .symbol_graph -> no index
    results = search_bm25("foo", str(tmp_path), top_k=10)
    assert results == []


def test_build_index_recursion_error_returns_false(tmp_path, monkeypatch):
    """When rank_bm25 import raises RecursionError (installed but unusable), build_bm25_index returns False."""
    import builtins
    import json

    from agent.retrieval.bm25_retriever import _reset_for_testing, build_bm25_index

    _reset_for_testing()
    (tmp_path / ".symbol_graph").mkdir()
    (tmp_path / ".symbol_graph" / "repo_map.json").write_text(
        json.dumps({"symbols": {"foo": {"file": "a.py"}}})
    )
    monkeypatch.setenv("SERENA_PROJECT_DIR", str(tmp_path))

    real_import = builtins.__import__

    def fake_import(name, *args, **kwargs):
        if name == "rank_bm25":
            raise RecursionError("simulated numpy/import loader recursion")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", fake_import)
    ok = build_bm25_index(str(tmp_path))
    assert ok is False
