from __future__ import annotations

from pathlib import Path

import pytest

from agent.execution.step_dispatcher import dispatch
from agent.memory.state import AgentState
from agent.retrieval.context_builder_v2 import assemble_reasoning_context


def _write(p: Path, text: str) -> None:
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text, encoding="utf-8")


def _mk_state(project_root: str) -> AgentState:
    return AgentState(
        instruction="",
        current_plan={"plan_id": "t", "steps": []},
        context={"project_root": project_root},
    )


def test_docs_search_candidates_includes_docs_excludes_tests_and_vendor(tmp_path: Path):
    root = tmp_path
    _write(root / "README.md", "# Root Readme\nInstall steps...\n")
    _write(root / "docs" / "guide.md", "# Guide\nUsage...\n")
    _write(root / "Docs" / "arch.md", "# Architecture\n")
    _write(root / "tests" / "test_docs.md", "# Should be excluded\n")
    _write(root / "node_modules" / "x.md", "# Should be excluded\n")
    _write(root / "vendor" / "y.md", "# Should be excluded\n")
    _write(root / ".venv" / "z.md", "# Should be excluded\n")
    _write(root / ".git" / "w.md", "# Should be excluded\n")

    state = _mk_state(str(root))
    out = dispatch(
        {"action": "SEARCH_CANDIDATES", "query": "readme docs install usage", "artifact_mode": "docs"},
        state,
    )
    assert out["success"] is True
    cands = out["output"]["candidates"]
    assert cands, "docs mode should return docs candidates"
    files = [c["file"] for c in cands]
    assert any(f.endswith("README.md") for f in files)
    assert any("/docs/" in f.lower() or "\\docs\\" in f.lower() for f in files)
    assert not any("/tests/" in f.lower() or "\\tests\\" in f.lower() for f in files)
    assert not any("node_modules" in f.lower() for f in files)
    assert not any("/vendor/" in f.lower() or "\\vendor\\" in f.lower() for f in files)
    assert not any("/.venv/" in f.lower() or "\\.venv\\" in f.lower() for f in files)
    assert not any("/.git/" in f.lower() or "\\.git\\" in f.lower() for f in files)


def test_docs_build_context_populates_ranked_context(tmp_path: Path):
    root = tmp_path
    _write(root / "README.md", "# Root Readme\nInstall steps...\nMore...\n")
    _write(root / "docs" / "guide.md", "# Guide\nUsage...\n")

    state = _mk_state(str(root))
    dispatch({"action": "SEARCH_CANDIDATES", "query": "install guide", "artifact_mode": "docs"}, state)
    out = dispatch({"action": "BUILD_CONTEXT", "artifact_mode": "docs"}, state)
    assert out["success"] is True
    ranked = state.context.get("ranked_context") or []
    assert ranked, "docs BUILD_CONTEXT should populate ranked_context"
    for entry in ranked:
        assert entry.get("artifact_type") == "doc"
        assert entry.get("symbol") == ""
        assert entry.get("snippet")


def test_docs_root_readme_ranks_above_nested_markdown(tmp_path: Path):
    root = tmp_path
    _write(root / "README.md", "# Root Readme\nDocs overview\n")
    _write(root / "sub" / "README.md", "# Sub Readme\nIrrelevant\n")
    _write(root / "docs" / "zzz.md", "# Z\nOther\n")

    state = _mk_state(str(root))
    out = dispatch({"action": "SEARCH_CANDIDATES", "query": "readme docs overview", "artifact_mode": "docs"}, state)
    cands = out["output"]["candidates"]
    assert cands
    top = cands[0]["file"]
    assert top.endswith(str((root / "README.md").resolve())), "repo-root README should strongly outrank others"


def test_code_mode_search_candidates_path_unchanged_when_artifact_mode_absent(monkeypatch, tmp_path: Path):
    root = tmp_path
    state = _mk_state(str(root))

    called = {"n": 0}

    def fake_search_candidates(query: str, project_root=None, state=None):
        called["n"] += 1
        return [{"file": "x.py", "symbol": "X", "snippet": "x", "score": 1.0, "source": "fake"}]

    monkeypatch.setattr("agent.retrieval.retrieval_pipeline.search_candidates", fake_search_candidates)

    out = dispatch({"action": "SEARCH_CANDIDATES", "query": "anything"}, state)
    assert out["success"] is True
    assert called["n"] == 1
    assert out["output"]["candidates"][0]["source"] == "fake"


def test_code_mode_search_candidates_same_when_artifact_mode_code_explicit(monkeypatch, tmp_path: Path):
    root = tmp_path
    state = _mk_state(str(root))

    called = {"n": 0}

    def fake_search_candidates(query: str, project_root=None, state=None):
        called["n"] += 1
        return [{"file": "x.py", "symbol": "X", "snippet": "x", "score": 1.0, "source": "fake"}]

    monkeypatch.setattr("agent.retrieval.retrieval_pipeline.search_candidates", fake_search_candidates)

    out = dispatch({"action": "SEARCH_CANDIDATES", "query": "anything", "artifact_mode": "code"}, state)
    assert out["success"] is True
    assert called["n"] == 1
    assert out["output"]["candidates"][0]["source"] == "fake"


def test_invalid_artifact_mode_fails_cleanly(tmp_path: Path):
    state = _mk_state(str(tmp_path))
    out = dispatch({"action": "SEARCH_CANDIDATES", "query": "x", "artifact_mode": "nope"}, state)
    assert out["success"] is False
    assert "Invalid artifact_mode" in (out.get("error") or "")


def test_docs_mode_explain_gate_never_calls_search_fn(monkeypatch, tmp_path: Path):
    root = tmp_path
    _write(root / "README.md", "# Root Readme\nInstall steps...\n")

    def boom(*args, **kwargs):
        raise AssertionError("_search_fn must not be called in docs mode")

    monkeypatch.setattr("agent.execution.step_dispatcher._search_fn", boom)

    # Force context gate path: no ranked_context initially.
    state = _mk_state(str(root))
    out = dispatch({"action": "EXPLAIN", "description": "Where are the docs?", "artifact_mode": "docs"}, state)
    # Model call may fail depending on environment; invariant is _search_fn not called.
    assert out is not None

def test_code_mode_build_context_calls_run_retrieval_pipeline_when_artifact_mode_absent(monkeypatch, tmp_path: Path):
    root = tmp_path
    state = _mk_state(str(root))
    state.context["query"] = "q"
    state.context["candidates"] = [{"file": "a.py", "symbol": "", "snippet": "x", "score": 1.0, "source": "bm25"}]

    called = {"n": 0}

    def fake_run_retrieval_pipeline(search_results, state, query=None):
        called["n"] += 1
        state.context["ranked_context"] = [{"file": "a.py", "symbol": "", "snippet": "x"}]
        return {}

    monkeypatch.setattr("agent.retrieval.retrieval_pipeline.run_retrieval_pipeline", fake_run_retrieval_pipeline)

    out = dispatch({"action": "BUILD_CONTEXT"}, state)
    assert out["success"] is True
    assert called["n"] == 1


def test_docs_mode_build_context_does_not_call_run_retrieval_pipeline(monkeypatch, tmp_path: Path):
    root = tmp_path
    _write(root / "README.md", "# Root Readme\nInstall steps...\n")
    state = _mk_state(str(root))
    dispatch({"action": "SEARCH_CANDIDATES", "query": "install", "artifact_mode": "docs"}, state)

    def boom(*args, **kwargs):
        raise AssertionError("run_retrieval_pipeline must not be called in docs mode")

    monkeypatch.setattr("agent.retrieval.retrieval_pipeline.run_retrieval_pipeline", boom)
    out = dispatch({"action": "BUILD_CONTEXT", "artifact_mode": "docs"}, state)
    assert out["success"] is True


def test_explain_compatibility_docs_ranked_context():
    ranked = [
        {
            "file": "README.md",
            "symbol": "",
            "snippet": "# Title\nUsage...\n",
            "artifact_type": "doc",
            "title": "Title",
            "line_start": 1,
            "line_end": 2,
        }
    ]
    out = assemble_reasoning_context(ranked, max_chars=2000)
    assert "FILE: README.md" in out
    assert "SNIPPET:" in out
    assert "Usage..." in out


def test_docs_lane_isolation_fence_no_code_retrieval_calls(monkeypatch, tmp_path: Path):
    """
    Hard isolation fence: docs lane must not touch code retrieval internals.

    We monkeypatch code retrieval entrypoints to raise; docs mode must still succeed for:
      - SEARCH_CANDIDATES
      - BUILD_CONTEXT
      - EXPLAIN (auto-context path)
    """
    root = tmp_path
    _write(root / "README.md", "# Root Readme\nInstall steps...\n")
    _write(root / "docs" / "guide.md", "# Guide\nUsage...\n")

    def boom(*args, **kwargs):
        raise AssertionError("boom: code retrieval internal was called from docs lane")

    # Dispatcher-level raw search path (must not be used in docs mode)
    monkeypatch.setattr("agent.execution.step_dispatcher._search_fn", boom)
    # Code retrieval pipeline (must not be used in docs mode)
    monkeypatch.setattr("agent.retrieval.retrieval_pipeline.run_retrieval_pipeline", boom)
    # Hybrid/code retrievers (must not be used in docs mode)
    monkeypatch.setattr("agent.retrieval.vector_retriever.search_by_embedding", boom)
    monkeypatch.setattr("agent.retrieval.graph_retriever.retrieve_symbol_context", boom)
    # Reranker construction path (should never be touched by docs lane)
    monkeypatch.setattr("agent.retrieval.reranker.reranker_factory.create_reranker", boom)
    # Serena/grep fallback path (should never be touched by docs lane)
    monkeypatch.setattr("agent.tools.serena_adapter.search_code", boom)

    # Make EXPLAIN deterministic: stub model routing and calls.
    from agent.models.model_types import ModelType

    monkeypatch.setattr("agent.execution.step_dispatcher.get_model_for_task", lambda *_a, **_k: ModelType.SMALL)
    monkeypatch.setattr("agent.execution.step_dispatcher.call_small_model", lambda *_a, **_k: "ok")
    monkeypatch.setattr("agent.execution.step_dispatcher.call_reasoning_model", lambda *_a, **_k: "ok")

    state = _mk_state(str(root))

    # docs SEARCH_CANDIDATES must still succeed
    out = dispatch({"action": "SEARCH_CANDIDATES", "query": "install guide", "artifact_mode": "docs"}, state)
    assert out["success"] is True
    assert out["output"]["candidates"]

    # docs BUILD_CONTEXT must still succeed
    out2 = dispatch({"action": "BUILD_CONTEXT", "artifact_mode": "docs"}, state)
    assert out2["success"] is True
    ranked = state.context.get("ranked_context") or []
    assert ranked
    assert all(r.get("artifact_type") == "doc" for r in ranked)

    # docs EXPLAIN auto-context path must still succeed (and not call any patched internals)
    state2 = _mk_state(str(root))
    out3 = dispatch({"action": "EXPLAIN", "description": "How do I install?", "artifact_mode": "docs"}, state2)
    assert out3["success"] is True
    assert isinstance(out3.get("output"), str) and out3["output"].strip()

