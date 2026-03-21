"""Metadata survives prune/compress and explain assembly has typed signal (Phase C)."""

from __future__ import annotations

from unittest.mock import patch

from agent.memory.state import AgentState
from agent.repo_intelligence.context_compressor import compress_context
from agent.retrieval.context_pruner import prune_context
from agent.retrieval.result_contract import RETRIEVAL_RESULT_TYPE_SYMBOL_BODY
from agent.execution.step_dispatcher import _format_explain_context


def test_prune_preserves_typed_fields():
    rows = [
        {
            "file": "a.py",
            "symbol": "f",
            "snippet": "def f(): pass",
            "candidate_kind": "symbol",
            "retrieval_result_type": RETRIEVAL_RESULT_TYPE_SYMBOL_BODY,
            "implementation_body_present": True,
            "line_range": [1, 3],
            "relations": [{"kind": "ownership", "target_file": "a.py"}],
        }
    ]
    out = prune_context(rows, max_snippets=5, max_chars=8000)
    assert len(out) == 1
    assert out[0].get("retrieval_result_type") == RETRIEVAL_RESULT_TYPE_SYMBOL_BODY
    assert out[0].get("relations")


def test_compress_preserves_relations():
    rows = [
        {
            "file": "a.py",
            "snippet": "x" * 5000,
            "candidate_kind": "symbol",
            "relations": [{"kind": "import", "target_file": "b.py", "target_symbol": "t"}],
            "retrieval_result_type": RETRIEVAL_RESULT_TYPE_SYMBOL_BODY,
            "implementation_body_present": True,
        }
    ]
    out, ratio = compress_context(rows, repo_summary={"x": 1}, task_goal="t", max_tokens=50)
    assert out
    assert out[0].get("relations")


def test_explain_format_warns_without_typed_rows():
    st = AgentState(instruction="explain", current_plan={})
    st.context = {
        "ranked_context": [
            {"file": "a.py", "snippet": "plain only", "symbol": ""},
        ]
    }
    with patch("agent.execution.step_dispatcher.assemble_reasoning_context", return_value="body"):
        with patch("agent.execution.step_dispatcher.logger") as log:
            _ = _format_explain_context(st)
            assert log.warning.called


def test_explain_format_no_warn_when_typed():
    st = AgentState(instruction="explain", current_plan={})
    st.context = {
        "ranked_context": [
            {
                "file": "a.py",
                "snippet": "def f(): pass",
                "symbol": "f",
                "candidate_kind": "symbol",
                "retrieval_result_type": RETRIEVAL_RESULT_TYPE_SYMBOL_BODY,
            },
        ]
    }
    with patch("agent.execution.step_dispatcher.assemble_reasoning_context", return_value="ok"):
        with patch("agent.execution.step_dispatcher._filter_stub_placeholders_when_impl_exists", side_effect=lambda x: x):
            with patch("agent.execution.step_dispatcher.logger") as log:
                txt = _format_explain_context(st)
                assert "ok" in txt or "CONTEXT" in txt
                assert not log.warning.called
