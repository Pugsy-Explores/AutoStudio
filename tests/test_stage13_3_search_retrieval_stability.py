"""Stage 13.3: BM25 import safety, retrieval snippet coercion, safe telemetry JSON, rerank fallback."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock

from agent.memory.state import AgentState
from agent.observability.json_sanitize import json_safe_tree
from agent.retrieval.snippet_text import coerce_snippet_text


def test_coerce_snippet_text_plain_and_nested_list():
    assert coerce_snippet_text("abc") == "abc"
    assert coerce_snippet_text(["a", "b"]) == "a b"
    assert len(coerce_snippet_text("x" * 50_000)) == 20_000


def test_json_safe_tree_depth_and_cycle():
    a: dict = {"k": 1}
    a["self"] = a
    out = json_safe_tree(a, max_depth=10)
    assert out["self"] == "<cycle>"
    deep = {}
    cur: dict = deep
    for _ in range(100):
        n: dict = {}
        cur["n"] = n
        cur = n
    out2 = json_safe_tree(deep, max_depth=5)
    assert json.dumps(out2)  # must not recurse


def test_harness_serialize_loop_output_no_recursion_on_cycle():
    from tests.agent_eval.harness import _serialize_loop_output

    loop = {"edit_telemetry": {}}
    loop["edit_telemetry"] = loop  # type: ignore[assignment]
    snap = _serialize_loop_output(loop)
    assert isinstance(snap, dict)
    assert "_serialization_error" not in snap or snap.get("_serialization_error") is not True


def test_rerank_failure_falls_back_to_retriever_order(monkeypatch, tmp_path: Path):
    """Reranker raises once; pipeline completes with retriever ordering and telemetry flags."""
    import agent.retrieval.retrieval_pipeline as rp

    from agent.retrieval.reranker.reranker_factory import _reset_for_testing

    _reset_for_testing()

    py = tmp_path / "mod.py"
    py.write_text("def f():\n    return 1\n")

    raw = [{"file": str(py), "symbol": "f", "snippet": "def f():\n    return 1\n"}]
    state = AgentState(
        instruction="test",
        current_plan={"plan_id": "p", "steps": []},
        context={"project_root": str(tmp_path), "trace_id": "t13"},
    )

    monkeypatch.setattr(rp, "filter_and_rank_search_results", lambda r, q, pr: list(r))
    monkeypatch.setattr(rp, "detect_anchors", lambda r, q: list(r))
    monkeypatch.setattr(rp, "ENABLE_LOCALIZATION_ENGINE", False)

    def _expand(res):
        return [{"file": str(py), "symbol": "f", "action": "read_symbol_body", "line": None}]

    monkeypatch.setattr(rp, "expand_search_results", _expand)
    monkeypatch.setattr(rp, "read_symbol_body", lambda *a, **k: "def f():\n    return 1\n")
    monkeypatch.setattr(rp, "find_referencing_symbols", lambda *a, **k: [])

    def _built(*a, **k):
        return {
            "symbols": [{"file": str(py), "symbol": f"s{i}", "snippet": f"body{i}"} for i in range(8)],
            "references": [],
            "files": [],
            "snippets": [],
        }

    monkeypatch.setattr(rp, "build_context_from_symbols", _built)
    monkeypatch.setattr(rp, "expand_from_anchors", lambda *a, **k: [])
    monkeypatch.setattr(rp, "deduplicate_candidates", lambda c: c)
    monkeypatch.setattr(rp, "prune_context", lambda ctx, **kw: list(ctx)[:20])

    class Boom:
        def rerank(self, q, docs):
            raise RuntimeError("inference failed")

    monkeypatch.setattr(rp, "create_reranker", lambda: Boom())
    monkeypatch.setattr(rp, "RERANKER_ENABLED", True)
    monkeypatch.setattr(rp, "RERANK_MIN_CANDIDATES", 1)
    monkeypatch.setattr(rp, "is_symbol_query", lambda q: (False, None))

    out = rp.run_retrieval_pipeline(raw, state, query="find f")
    assert isinstance(out, dict)
    assert state.context.get("reranker_failed") is True
    assert state.context.get("reranker_failed_fallback_used") is True
    assert state.context.get("ranked_context")


def test_rank_bm25_import_recursion_marks_bm25_unavailable(monkeypatch):
    """rank_bm25 -> numpy can raise RecursionError in some import orders; pipeline must not crash."""
    import builtins

    real = builtins.__import__

    def fake(name, globals=None, locals=None, fromlist=(), level=0):
        if name == "rank_bm25":
            raise RecursionError("simulated numpy import recursion")
        return real(name, globals, locals, fromlist, level)

    monkeypatch.setattr(builtins, "__import__", fake)
    from agent.retrieval.retrieval_pipeline import run_retrieval_pipeline

    state = AgentState(
        instruction="x",
        current_plan={"plan_id": "p", "steps": []},
        context={"project_root": ".", "trace_id": "t"},
    )
    out = run_retrieval_pipeline([], state, query="q")
    assert state.context.get("bm25_available") is False
    assert out.get("results") == []


def test_expand_symbol_dependencies_cycle_safe():
    from repo_graph.graph_query import expand_symbol_dependencies
    from repo_graph.graph_storage import GraphStorage

    storage = MagicMock()
    storage.get_symbol.side_effect = lambda sid: {"id": sid, "name": "n", "file": "f.py"}
    n1 = {"id": 2, "name": "a", "file": "a.py"}
    storage.get_neighbors.side_effect = lambda nid, **kw: [n1] if nid == 1 else []

    nodes, tel = expand_symbol_dependencies(1, storage, depth=3, max_nodes=10, max_symbol_expansions=20)
    assert len(nodes) >= 1
    assert tel.get("graph_nodes_expanded", 0) >= 1
