"""Typed candidate metadata, grounding readiness, and propagation (Phases 1–3)."""

from __future__ import annotations

from unittest.mock import patch

from agent.contracts.error_codes import REASON_CODE_INSUFFICIENT_GROUNDING
from agent.execution.explain_gate import code_explain_grounding_ready
from agent.memory.state import AgentState
from agent.retrieval.context_pruner import prune_context
from agent.retrieval.result_contract import RETRIEVAL_RESULT_TYPE_SYMBOL_BODY, normalize_result
from agent.retrieval.retrieval_pipeline import _build_candidates_from_context


def test_normalize_result_preserves_candidate_kind_and_scores():
    n = normalize_result(
        {
            "file": "a.py",
            "symbol": "x",
            "line": 1,
            "snippet": "s",
            "candidate_kind": "symbol",
            "line_range": [1, 5],
            "source": "loc",
            "localization_score": 0.0,
        }
    )
    assert n["candidate_kind"] == "symbol"
    assert n["line_range"] == [1, 5]
    assert n["source"] == "loc"
    assert n["localization_score"] == 0.0


def test_build_candidates_propagates_symbol_body_fields():
    built = {
        "symbols": [
            {
                "file": "/x.py",
                "symbol": "foo",
                "snippet": "def foo():\n  pass\n",
                "retrieval_result_type": RETRIEVAL_RESULT_TYPE_SYMBOL_BODY,
                "implementation_body_present": True,
                "candidate_kind": "symbol",
            }
        ],
        "references": [],
        "snippets": [],
    }
    cands = _build_candidates_from_context(built)
    assert len(cands) == 1
    assert cands[0]["retrieval_result_type"] == RETRIEVAL_RESULT_TYPE_SYMBOL_BODY
    assert cands[0]["implementation_body_present"] is True
    assert cands[0]["candidate_kind"] == "symbol"


def test_prune_context_preserves_metadata():
    rows = [
        {
            "file": "a.py",
            "symbol": "s",
            "snippet": "def s():\n  return 1\n",
            "retrieval_result_type": RETRIEVAL_RESULT_TYPE_SYMBOL_BODY,
            "implementation_body_present": True,
            "candidate_kind": "symbol",
        }
    ]
    out = prune_context(rows, max_snippets=5, max_chars=8000)
    assert out[0].get("retrieval_result_type") == RETRIEVAL_RESULT_TYPE_SYMBOL_BODY
    assert out[0].get("candidate_kind") == "symbol"


def test_pruner_keeps_minimum_context():
    """Large impl-body row near budget -> still partially included (MIN_FALLBACK_CHARS)."""
    # First row exhausts most budget; second has implementation_body_present.
    # Sort: symbol before file, so both symbol → order by index.
    big_snippet = "x" * 5000
    impl_snippet = "def create():\n    pass\n" + "y" * 3000
    rows = [
        {"file": "other.py", "snippet": big_snippet, "candidate_kind": "symbol"},
        {
            "file": "sessions.py",
            "symbol": "create",
            "snippet": impl_snippet,
            "implementation_body_present": True,
            "candidate_kind": "symbol",
        },
    ]
    # Budget 5050: first row uses 5000, remaining=50. 50 < 80, impl_body -> include min(50,40)=40
    out = prune_context(rows, max_snippets=10, max_chars=5050)
    assert len(out) >= 2
    impl_in_out = next((r for r in out if r.get("file") == "sessions.py"), None)
    assert impl_in_out is not None, "impl-body row must not be fully dropped"
    assert len(impl_in_out.get("snippet", "")) >= 1, "minimal slice must be included"


def test_code_explain_grounding_ready_typed_symbol_body():
    st = AgentState(
        instruction="t",
        current_plan={"plan_id": "p", "steps": []},
        context={
            "ranked_context": [
                {
                    "file": "z.py",
                    "snippet": "x",
                    "retrieval_result_type": RETRIEVAL_RESULT_TYPE_SYMBOL_BODY,
                }
            ]
        },
    )
    ok, sig = code_explain_grounding_ready({"artifact_mode": "code"}, st)
    assert ok is True
    assert sig.get("grounding") == "typed_symbol_body"


def test_code_explain_grounding_ready_implementation_flag():
    st = AgentState(
        instruction="t",
        current_plan={"plan_id": "p", "steps": []},
        context={
            "ranked_context": [
                {"file": "z.py", "snippet": " ", "implementation_body_present": True},
            ]
        },
    )
    ok, sig = code_explain_grounding_ready({"artifact_mode": "code"}, st)
    assert ok is True
    assert sig.get("grounding") == "typed_symbol_body"


def test_code_explain_grounding_ready_heuristic():
    st = AgentState(
        instruction="t",
        current_plan={"plan_id": "p", "steps": []},
        context={
            "ranked_context": [
                {"file": "z.py", "snippet": "def f():\n  pass\n"},
            ]
        },
    )
    ok, sig = code_explain_grounding_ready({"artifact_mode": "code"}, st)
    assert ok is True
    assert sig.get("grounding") == "heuristic"


def test_code_explain_grounding_ready_not_ready():
    st = AgentState(
        instruction="t",
        current_plan={"plan_id": "p", "steps": []},
        context={
            "ranked_context": [
                {"file": "z.py", "snippet": "onlyBare"},
            ],
        },
    )
    ok, sig = code_explain_grounding_ready({"artifact_mode": "code"}, st)
    assert ok is False
    assert sig.get("reason_code") == REASON_CODE_INSUFFICIENT_GROUNDING


def test_code_explain_grounding_ready_docs_lane_skips():
    st = AgentState(
        instruction="t",
        current_plan={"plan_id": "p", "steps": []},
        context={"ranked_context": []},
    )
    ok, sig = code_explain_grounding_ready({"artifact_mode": "docs"}, st)
    assert ok is True
    assert sig == {}


def test_build_context_preserves_metadata_on_search_results():
    from agent.tools.build_context import build_context

    st = AgentState(
        instruction="task",
        current_plan={"plan_id": "p", "steps": []},
        context={
            "project_root": "/tmp",
            "candidates": [
                {
                    "file": "a.py",
                    "symbol": "x",
                    "snippet": "snip",
                    "candidate_kind": "symbol",
                    "retrieval_result_type": RETRIEVAL_RESULT_TYPE_SYMBOL_BODY,
                    "implementation_body_present": True,
                    "line_range": [1, 3],
                }
            ],
            "query": "q",
        },
    )
    with patch("agent.retrieval.retrieval_pipeline.run_retrieval_pipeline") as rpp:
        build_context(candidates=None, state=st, artifact_mode="code")
    call_args = rpp.call_args
    sr = call_args[0][0]
    assert len(sr) == 1
    assert sr[0].get("candidate_kind") == "symbol"
    assert sr[0].get("retrieval_result_type") == RETRIEVAL_RESULT_TYPE_SYMBOL_BODY
    assert sr[0].get("line_range") == [1, 3]


def test_localization_output_has_candidate_kind():
    from agent.retrieval.localization.localization_engine import localize_issue

    ranked = [{"file": "a.py", "symbol": "AnchorSym", "snippet": "x", "name": "AnchorSym"}]
    with patch(
        "agent.retrieval.localization.localization_engine.traverse_dependencies"
    ) as td:
        td.return_value = {
            "candidate_symbols": [
                {"file": "b.py", "name": "S", "docstring": "", "hop_distance": 1},
            ],
            "candidate_files": [],
            "node_count": 1,
        }
        with patch(
            "agent.retrieval.localization.localization_engine.build_execution_paths"
        ) as bep:
            bep.return_value = []
            with patch(
                "agent.retrieval.localization.localization_engine.rank_localization_candidates"
            ) as rk:

                def _pass(cands, *_args, **_kwargs):
                    return cands

                rk.side_effect = _pass
                out = localize_issue("q", ranked, "/tmp", trace_id="")
    assert len(out) == 1
    assert out[0].get("candidate_kind") == "localization"

