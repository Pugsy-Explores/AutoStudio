"""Stage 47: substantive context predicate + EXPLAIN dispatch gating (code lane)."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from agent.contracts.error_codes import REASON_CODE_INSUFFICIENT_SUBSTANTIVE_CONTEXT
from agent.execution.explain_gate import (
    GRAPH_PLACEHOLDER_SNIPPET_PREFIX,
    has_substantive_code_context,
    ranked_row_is_substantive_for_code_explain,
)
from agent.execution.step_dispatcher import dispatch
from agent.memory.state import AgentState
from agent.retrieval.result_contract import RETRIEVAL_RESULT_TYPE_SYMBOL_BODY


def test_graph_placeholder_only_false():
    row = {"file": "a.py", "snippet": f"{GRAPH_PLACEHOLDER_SNIPPET_PREFIX} run"}
    assert not ranked_row_is_substantive_for_code_explain(row)
    assert not has_substantive_code_context([row])


def test_graph_placeholder_settings_false():
    row = {"file": "b.py", "snippet": f"{GRAPH_PLACEHOLDER_SNIPPET_PREFIX} Settings"}
    assert not has_substantive_code_context([row])


def test_empty_snippet_false():
    assert not ranked_row_is_substantive_for_code_explain({"file": "x.py", "snippet": ""})
    assert not ranked_row_is_substantive_for_code_explain({"file": "x.py", "snippet": "   "})


def test_whitespace_only_false():
    assert not has_substantive_code_context([{"file": "z.py", "snippet": "\n\t  \n"}])


def test_real_code_snippet_true():
    row = {"file": "agent/x.py", "snippet": "def dispatch(step, state):\n    return None\n"}
    assert ranked_row_is_substantive_for_code_explain(row)
    assert has_substantive_code_context([row])


def test_mixed_placeholder_and_real_true():
    ph = {"file": "a.py", "snippet": f"{GRAPH_PLACEHOLDER_SNIPPET_PREFIX} foo"}
    real = {"file": "b.py", "snippet": "class Engine:\n    pass\n"}
    assert has_substantive_code_context([ph, real])


def test_implementation_body_flag_true():
    row = {"file": "c.py", "snippet": "x", "implementation_body_present": True}
    assert ranked_row_is_substantive_for_code_explain(row)


def test_symbol_body_type_true():
    row = {
        "file": "d.py",
        "snippet": "x",
        "retrieval_result_type": RETRIEVAL_RESULT_TYPE_SYMBOL_BODY,
    }
    assert ranked_row_is_substantive_for_code_explain(row)


def test_long_prose_without_code_tokens_true():
    """Code-lane EXPLAIN: long README-style prose counts as substantive (deterministic)."""
    prose = (
        "This package provides configuration loading from environment variables "
        "and merges defaults with user overrides for the runtime engine."
    )
    assert len(prose) >= 48
    row = {"file": "README.md", "snippet": prose}
    assert ranked_row_is_substantive_for_code_explain(row)
    assert has_substantive_code_context([row])


def test_short_prose_without_code_false():
    row = {"file": "README.md", "snippet": "See the documentation for details."}
    assert not ranked_row_is_substantive_for_code_explain(row)


def test_bare_identifier_rejected():
    assert not ranked_row_is_substantive_for_code_explain({"file": "m.py", "snippet": "run"})
    assert not ranked_row_is_substantive_for_code_explain({"file": "m.py", "snippet": "Settings"})


def test_non_dict_rows_ignored():
    assert not has_substantive_code_context([None, "x", 1])  # type: ignore[list-item]


def test_empty_list_false():
    assert not has_substantive_code_context([])
    assert not has_substantive_code_context(None)  # type: ignore[arg-type]


def _code_state(**ctx):
    base = {
        "project_root": "/tmp",
        "dominant_artifact_mode": "code",
        "lane_violations": [],
        "ranked_context": [],
    }
    base.update(ctx)
    return AgentState(
        instruction="task",
        current_plan={"plan_id": "p", "steps": []},
        context=base,
    )


class TestExplainDispatchSubstantiveGate:
    """Loop-prevention: placeholder-only ranked_context fails before LLM."""

    def test_placeholder_only_retryable_and_no_model(self):
        st = _code_state(
            ranked_context=[
                {"file": "a.py", "snippet": f"{GRAPH_PLACEHOLDER_SNIPPET_PREFIX} dispatch"},
            ]
        )
        with patch("agent.execution.step_dispatcher.call_reasoning_model") as crm:
            with patch("agent.execution.step_dispatcher.call_small_model") as csm:
                out = dispatch({"id": 1, "action": "EXPLAIN", "description": "explain dispatch"}, st)
        assert out["success"] is False
        assert "non-substantive context" in (out.get("error") or "")
        assert out.get("reason_code") == REASON_CODE_INSUFFICIENT_SUBSTANTIVE_CONTEXT
        crm.assert_not_called()
        csm.assert_not_called()

    def test_substantive_ranked_proceeds_to_model(self):
        st = _code_state(
            ranked_context=[
                {"file": "z.py", "snippet": "def dispatch():\n    return 1\n"},
            ]
        )
        with patch("agent.execution.step_dispatcher.call_reasoning_model", return_value="y" * 50):
            with patch("agent.execution.step_dispatcher.get_model_for_task") as gmt:
                gmt.return_value = MagicMock(value="REASONING")
                out = dispatch({"id": 1, "action": "EXPLAIN", "description": "explain"}, st)
        assert out.get("success") is True
        assert len(out.get("output") or "") >= 40

    def test_empty_ranked_preserves_inject_path_not_substantive_error(self):
        """Empty ranked_context hits existing inject / failure paths, not Stage 47 message."""
        st = _code_state(ranked_context=[])
        with patch("agent.execution.step_dispatcher._search_fn", return_value={"results": [], "query": "q"}):
            out = dispatch({"id": 1, "action": "EXPLAIN", "description": "explain foo"}, st)
        assert out["success"] is False
        assert "non-substantive" not in (out.get("error") or "")
        assert "No context for EXPLAIN" in (out.get("error") or "")

    def test_docs_lane_not_subject_to_substantive_gate(self):
        st = _code_state(
            ranked_context=[{"file": "x.md", "snippet": f"{GRAPH_PLACEHOLDER_SNIPPET_PREFIX} x"}],
        )
        st.context["dominant_artifact_mode"] = "docs"
        with patch("agent.execution.step_dispatcher.call_reasoning_model", return_value="y" * 50):
            with patch("agent.execution.step_dispatcher.get_model_for_task") as gmt:
                gmt.return_value = MagicMock(value="REASONING")
                out = dispatch(
                    {
                        "id": 1,
                        "action": "EXPLAIN",
                        "description": "d",
                        "artifact_mode": "docs",
                    },
                    st,
                )
        assert out.get("success") is True


class TestValidatorCoherence:
    """
    Validator: failed EXPLAIN (success=False) surfaces `error` to replanner — no change required
    to distinguish empty vs non-substantive; both are `not result.success` with distinct `error` text.
    """

    def test_validate_step_failed_explain_non_substantive(self):
        from agent.memory.step_result import StepResult
        from agent.orchestrator.validator import _validate_step_rules

        step = {"id": 1, "action": "EXPLAIN", "description": "x"}
        result = StepResult(
            step_id=1,
            action="EXPLAIN",
            success=False,
            output="",
            latency_seconds=0.0,
            error="EXPLAIN received non-substantive context. Add SEARCH for implementation code.",
        )
        valid, feedback = _validate_step_rules(step, result)
        assert valid is False
        assert "non-substantive" in feedback.lower()
