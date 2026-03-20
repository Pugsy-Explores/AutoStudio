"""Stage 15 — retrieval/search hardening tests for hierarchical explain/docs tasks."""

from __future__ import annotations

import pytest

from agent.retrieval.docs_retriever import build_docs_context, search_docs_candidates
from tests.agent_eval.harness import _two_phase_parent_plan
from tests.agent_eval.suites.core12 import load_core12
from tests.agent_eval.task_specs import resolve_repo_dir


def test_two_phase_plan_has_subgoal_as_query_for_search_candidates():
    """SEARCH_CANDIDATES must receive subgoal as query; empty query returns no candidates."""
    plan = _two_phase_parent_plan(
        "Read benchmark_local/TRACE_NOTE.md and src/requests/sessions.py. Write explain_out.txt.",
        parent_plan_id="test",
    )
    phases = plan.get("phases") or []
    assert len(phases) >= 1
    p0 = phases[0]
    steps = p0.get("steps") or []
    search_step = next((s for s in steps if (s.get("action") or "").upper() == "SEARCH_CANDIDATES"), None)
    assert search_step is not None
    query = search_step.get("query") or search_step.get("description") or ""
    assert len(query) > 0
    assert "documentation" in query.lower() or "trace" in query.lower() or "read" in query.lower()


def test_docs_search_returns_candidates_with_nonempty_query(tmp_path):
    """Docs retriever returns candidates when query is non-empty."""
    # Use a real fixture so we have README/docs
    specs = load_core12()
    spec = next(s for s in specs if "requests" in s.repo_path)
    root = resolve_repo_dir(spec)
    cands = search_docs_candidates("readme session request", str(root))
    assert isinstance(cands, list)
    # Should find README and possibly benchmark_local docs
    assert len(cands) > 0


def test_docs_search_returns_empty_with_empty_query(tmp_path):
    """Docs retriever returns empty when query is empty (regression guard)."""
    specs = load_core12()
    spec = next(s for s in specs if "requests" in s.repo_path)
    root = resolve_repo_dir(spec)
    cands = search_docs_candidates("", str(root))
    assert cands == []


def test_build_docs_context_prefers_source_docs_over_junk(tmp_path):
    """build_docs_context returns ranked entries from real docs, not index artifacts."""
    specs = load_core12()
    spec = next(s for s in specs if "click" in s.repo_path)
    root = resolve_repo_dir(spec)
    cands = search_docs_candidates("decorators benchmark stability", str(root))
    blocks = build_docs_context("decorators benchmark stability", str(root), candidates=cands)
    assert isinstance(blocks, list)
    for b in blocks:
        if isinstance(b, dict):
            f = str(b.get("file", ""))
            assert ".symbol_graph" not in f
            assert "__pycache__" not in f


def test_phase0_has_build_context_step():
    """Phase 0 docs lane must include BUILD_CONTEXT so ranked_context is populated."""
    plan = _two_phase_parent_plan("Find docs and explain the flow", parent_plan_id="test")
    phases = plan.get("phases") or []
    assert len(phases) >= 1
    steps = phases[0].get("steps") or []
    actions = [(s.get("action") or "").upper() for s in steps]
    assert "BUILD_CONTEXT" in actions
