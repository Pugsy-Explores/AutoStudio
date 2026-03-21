"""
SEARCH stack matrix (Stages 41–46.1): policy validity, query helpers, dispatcher _search_fn wiring.

Order: easy → harder integration. Does not replace focused tests in test_policy_engine / test_repo_map_lookup.
"""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

import agent.retrieval.graph_retriever as graph_retriever_mod

from agent.execution.policy_engine import _is_valid_search_result
from agent.execution.step_dispatcher import _search_fn
from agent.memory.state import AgentState
from agent.retrieval.query_rewriter import heuristic_condense_for_retrieval
from config.repo_graph_config import REPO_MAP_JSON, SYMBOL_GRAPH_DIR


def _make_state(project_root: str | None) -> AgentState:
    return AgentState(
        instruction="test",
        current_plan={"plan_id": "p", "steps": []},
        context={"project_root": project_root} if project_root else {},
    )


class TestIsValidSearchResultMatrix:
    """Mirror validator + policy: _is_valid_search_result edge cases."""

    def test_rejects_missing_file(self):
        assert not _is_valid_search_result([{"snippet": "x"}], None)

    def test_accepts_py_file_empty_snippet(self):
        assert _is_valid_search_result([{"file": "agent/x.py", "snippet": ""}], None)

    def test_rejects_non_py_empty_snippet(self):
        assert not _is_valid_search_result([{"file": "README.md", "snippet": ""}], None)

    def test_accepts_non_py_with_nonempty_snippet(self):
        assert _is_valid_search_result([{"file": "README.md", "snippet": "hello"}], None)

    def test_rejects_empty_results_list(self):
        assert not _is_valid_search_result([], None)

    def test_raw_none_treated_like_no_marker_for_shape(self):
        hits = [{"file": "a.py", "snippet": "ok"}]
        assert _is_valid_search_result(hits, None)
        assert _is_valid_search_result(hits, {"results": hits, "query": "q"})

    def test_malformed_first_row_missing_file(self):
        assert not _is_valid_search_result([{"snippet": "only"}], None)


class TestHeuristicCondenseForRetrieval:
    """Deterministic condensing used in retrieval / rewriter paths."""

    def test_strips_filler_words(self):
        assert "find" not in heuristic_condense_for_retrieval("find the StepExecutor").lower().split()

    def test_whitespace_only_returns_empty(self):
        assert heuristic_condense_for_retrieval("   \t") == ""

    def test_preserves_symbol_like_tokens(self):
        out = heuristic_condense_for_retrieval("Where is StepExecutor defined")
        assert "StepExecutor" in out or "stepexecutor" in out.lower()


class TestSearchFnIntegration:
    """Dispatcher _search_fn: hybrid, repo_map context, fallbacks (mocked)."""

    def test_hybrid_short_circuit_returns_merged_results(self, tmp_path: Path):
        root = str(tmp_path)
        state = _make_state(root)
        fake = {"results": [{"file": str(tmp_path / "a.py"), "snippet": "hit", "line": 1}], "query": "q"}
        with patch("agent.execution.step_dispatcher.ENABLE_HYBRID_RETRIEVAL", True):
            with patch("agent.retrieval.search_pipeline.hybrid_retrieve", return_value=fake) as hy:
                out = _search_fn("test query", state)
        hy.assert_called_once()
        assert out.get("results")
        assert out["results"][0].get("file")

    def test_falls_back_to_sequential_when_hybrid_empty(self, tmp_path: Path):
        """Hybrid returns nothing; sequential retrievers run; grep uses patched search_code."""
        import agent.execution.step_dispatcher as sd

        root = str(tmp_path)
        state = _make_state(root)
        serena_hit = {
            "results": [{"file": str(tmp_path / "g.py"), "snippet": "sym", "line": 1}],
            "query": "lookup symbol",
        }
        with patch("agent.execution.step_dispatcher.ENABLE_HYBRID_RETRIEVAL", True):
            with patch("agent.execution.step_dispatcher.ENABLE_VECTOR_SEARCH", False):
                with patch("agent.retrieval.search_pipeline.hybrid_retrieve", return_value={"results": [], "query": "q"}):
                    with patch.object(graph_retriever_mod, "retrieve_symbol_context", return_value={"results": []}):
                        with patch.object(sd, "search_code", return_value=serena_hit):
                            with patch(
                                "agent.retrieval.search_target_filter.filter_and_rank_search_results",
                                side_effect=lambda res, *a, **kw: res,
                            ):
                                out = _search_fn("lookup symbol", state)
        assert len(out.get("results") or []) >= 1

    def test_repo_map_candidates_populated_when_map_exists(self, tmp_path: Path):
        d = tmp_path / SYMBOL_GRAPH_DIR
        d.mkdir(parents=True)
        (d / REPO_MAP_JSON).write_text(
            json.dumps({"symbols": {"AlphaBeta": {"file": "a.py"}}}),
            encoding="utf-8",
        )
        root = str(tmp_path)
        state = _make_state(root)
        with patch("agent.execution.step_dispatcher.ENABLE_HYBRID_RETRIEVAL", False):
            with patch("agent.retrieval.graph_retriever.retrieve_symbol_context", return_value={"results": []}):
                with patch("agent.tools.search_code", return_value={"results": [], "query": "q"}):
                    with patch("agent.execution.step_dispatcher.list_files", return_value=[]):
                        _search_fn("AlphaBeta", state)
        cands = state.context.get("repo_map_candidates") or []
        assert any(c.get("anchor") == "AlphaBeta" for c in cands)

    def test_file_search_fallback_marker_when_all_empty(self, tmp_path: Path):
        """Last-resort file listing: retrieval_fallback=file_search (policy treats as non-success)."""
        (tmp_path / "only_file.py").write_text("# x", encoding="utf-8")
        root = str(tmp_path)
        state = _make_state(root)
        with patch("agent.execution.step_dispatcher.ENABLE_HYBRID_RETRIEVAL", False):
            with patch("agent.retrieval.graph_retriever.retrieve_symbol_context", return_value={"results": []}):
                with patch("agent.tools.search_code", return_value={"results": [], "query": "q"}):
                    out = _search_fn("no_match_query_xyz", state)
        assert out.get("retrieval_fallback") == "file_search"
        assert out.get("results")

    def test_list_dir_branch_emits_marker(self, tmp_path: Path):
        sub = tmp_path / "pkg"
        sub.mkdir()
        (sub / "__init__.py").write_text("", encoding="utf-8")
        root = str(tmp_path)
        state = _make_state(root)
        state.context["chosen_tool"] = "list_dir"
        with patch("agent.execution.step_dispatcher.ENABLE_HYBRID_RETRIEVAL", False):
            with patch("agent.retrieval.graph_retriever.retrieve_symbol_context", return_value={"results": []}):
                with patch("agent.tools.search_code", return_value={"results": [], "query": "q"}):
                    out = _search_fn("pkg", state)
        assert out.get("retrieval_fallback") == "list_dir"
        assert out.get("query") == "pkg"


class TestSearchFilterPreservesMarkers:
    """filter_and_rank runs on non-empty results; fallback markers remain on dict."""

    def test_marker_preserved_when_results_nonempty(self, tmp_path: Path):
        root = str(tmp_path)
        state = _make_state(root)
        payload = {
            "results": [{"file": str(tmp_path / "a.py"), "snippet": "x", "line": 0}],
            "query": "q",
            "retrieval_fallback": "file_search",
        }
        with patch("agent.execution.step_dispatcher.ENABLE_HYBRID_RETRIEVAL", True):
            with patch("agent.retrieval.search_pipeline.hybrid_retrieve", return_value=payload):
                with patch("agent.retrieval.search_target_filter.filter_and_rank_search_results", side_effect=lambda r, *a, **k: r):
                    out = _search_fn("q", state)
        assert out.get("retrieval_fallback") == "file_search"
