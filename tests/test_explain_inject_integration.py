"""
EXPLAIN inject — integration-style contracts (fixture repo / tmp workspace).

- Uses the real `dispatch` entrypoint and production import graph.
- Mocks only LLM calls and selected retrieval internals for determinism.
- Marked `integration` + `explain_inject` for selective runs.

Run:
  python3 -m pytest tests/test_explain_inject_integration.py -v
  python3 -m pytest tests/test_explain_inject_integration.py -v -m explain_inject
  python3 -m tests.agent_eval.run_explain_inject
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from agent.execution.step_dispatcher import dispatch
from agent.memory.state import AgentState


pytestmark = [pytest.mark.integration, pytest.mark.explain_inject]


def _state(root: str) -> AgentState:
    return AgentState(
        instruction="integration",
        current_plan={"plan_id": "p", "steps": []},
        context={
            "project_root": root,
            "dominant_artifact_mode": "code",
            "lane_violations": [],
            "ranked_context": [],
        },
    )


def test_explain_inject_through_real_dispatcher_stack_tmp_repo(tmp_path: Path):
    """
    End-to-end: tmp workspace, `_search_fn` sequential path returns a grep hit,
    `run_retrieval_pipeline` runs, LLM stub returns long enough output for sanity.
    """
    mod = tmp_path / "handler.py"
    mod.write_text("def dispatch_steps():\n    return 1\n", encoding="utf-8")
    root = str(tmp_path)
    state = _state(root)
    hit = {
        "results": [{"file": str(mod), "snippet": "def dispatch_steps", "line": 1}],
        "query": "dispatch_steps",
    }
    import agent.execution.step_dispatcher as sd
    import agent.retrieval.graph_retriever as gr

    with patch.object(sd, "ENABLE_HYBRID_RETRIEVAL", False):
        with patch.object(gr, "retrieve_symbol_context", return_value={"results": []}):
            with patch.object(sd, "search_code", return_value=hit):
                with patch(
                    "agent.retrieval.search_target_filter.filter_and_rank_search_results",
                    side_effect=lambda res, *a, **k: res,
                ):
                    with patch.object(sd, "call_reasoning_model", return_value="y" * 45) as crm:
                        with patch.object(sd, "get_model_for_task") as gmt:
                            gmt.return_value = MagicMock(value="REASONING")
                            out = dispatch(
                                {
                                    "id": 1,
                                    "action": "EXPLAIN",
                                    "description": "explain how dispatch_steps works",
                                },
                                state,
                            )
    assert out.get("success") is True
    assert len((out.get("output") or "")) >= 40
    assert crm.called
    prompt = crm.call_args[0][0]
    assert "dispatch_steps" in prompt or "BEGIN CONTEXT" in prompt


def test_explain_prior_ranked_context_skips_retrieval_integration(tmp_path: Path):
    """With non-empty `ranked_context`, no `search_code` / `_search_fn` path."""
    state = _state(str(tmp_path))
    state.context["ranked_context"] = [
        {"file": "injected.py", "snippet": "def payload():\n    return 1\n"},
    ]
    import agent.execution.step_dispatcher as sd

    with patch.object(sd, "search_code") as sc:
        with patch("agent.execution.step_dispatcher._search_fn") as sfn:
            with patch("agent.execution.step_dispatcher.call_reasoning_model", return_value="z" * 50):
                with patch("agent.execution.step_dispatcher.get_model_for_task") as gmt:
                    gmt.return_value = MagicMock(value="REASONING")
                    dispatch({"id": 1, "action": "EXPLAIN", "description": "explain injected"}, state)
    sc.assert_not_called()
    sfn.assert_not_called()


def test_typo_target_single_shot_failure_no_second_query(tmp_path: Path):
    """
    Current limitation: inject calls `_search_fn` once. Empty first result → failure;
    no second attempt (SEARCH policy could rewrite/retry).
    """
    state = _state(str(tmp_path))
    calls = []

    def once(q, s):
        calls.append(q)
        return {"results": [], "query": q}

    with patch("agent.execution.step_dispatcher._search_fn", side_effect=once):
        out = dispatch(
            {
                "id": 1,
                "action": "EXPLAIN",
                "description": "explain ZZZNonexistentSymbol99999",
            },
            state,
        )
    assert out.get("success") is False
    assert len(calls) == 1


def test_directory_listing_marker_not_valid_context_integration(tmp_path: Path):
    """Aligns with policy: `file_search` / `list_dir` markers fail `_is_valid_search_result`."""
    state = _state(str(tmp_path))
    raw = {
        "results": [{"file": str(tmp_path / "a.py"), "snippet": "x"}],
        "retrieval_fallback": "file_search",
        "query": "q",
    }
    with patch("agent.execution.step_dispatcher._search_fn", return_value=raw):
        out = dispatch({"id": 1, "action": "EXPLAIN", "description": "explain something"}, state)
    assert out.get("success") is False
