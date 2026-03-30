"""
EXPLAIN code-lane injected retrieval: production-behavior matrix (easy → hard).

Covers the path in `dispatch` when `ranked_context` is empty: single `_search_fn`,
`_is_valid_search_result`, optional `run_retrieval_pipeline`, asymmetry vs SEARCH.

Does not assert model wording. Complements `test_explain_query_shaping.py` (shaping helpers)
and `test_search_stack_matrix.TestIsValidSearchResultMatrix` (shared validity rules).
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from agent.execution.explain_gate import ensure_context_before_explain
from agent.execution.policy_engine import ExecutionPolicyEngine, _is_valid_search_result
from agent.execution.step_dispatcher import (
    EXPLAIN_NEEDS_CONTEXT_PREFIX,
    _shape_query_for_explain_retrieval,
    dispatch,
)
from agent.memory.state import AgentState

pytestmark = pytest.mark.explain_inject


def _code_state(*, ranked_context: list | None = None, project_root: str = "/tmp", **extra) -> AgentState:
    ctx = {
        "project_root": project_root,
        "dominant_artifact_mode": "code",
        "lane_violations": [],
        "ranked_context": ranked_context if ranked_context is not None else [],
    }
    ctx.update(extra)
    return AgentState(
        instruction="user task",
        current_plan={"plan_id": "p", "steps": []},
        context=ctx,
    )


def _valid_hit():
    return {"results": [{"file": "agent/x.py", "snippet": "def f(): pass"}], "query": "q"}


def _inject_pipeline_sets_substantive_ranked(results, state, query=None):
    """Mock `run_retrieval_pipeline`: production fills ranked_context; inject tests must too."""
    state.context["ranked_context"] = [
        {"file": "/tmp/agent/x.py", "snippet": "def injected_ctx():\n    return 0\n"},
    ]
    return {}


class TestEnsureContextBeforeExplainSemantics:
    """Current `ensure_context_before_explain`: only `ranked_context` length matters."""

    def test_ranked_non_empty_has_context(self):
        st = _code_state(ranked_context=[{"file": "a.py", "snippet": "x"}])
        has_ctx, synthetic = ensure_context_before_explain(
            {"id": 1, "action": "EXPLAIN", "description": "d"}, st
        )
        assert has_ctx is True
        assert synthetic is None

    def test_ranked_empty_no_context_even_with_search_memory(self):
        st = _code_state(
            ranked_context=[],
            search_memory={"query": "prior", "results": [{"file": "b.py", "snippet": "y"}]},
        )
        has_ctx, synthetic = ensure_context_before_explain(
            {"id": 1, "action": "EXPLAIN", "description": "explain foo"}, st
        )
        assert has_ctx is False
        assert synthetic is not None
        assert synthetic.get("action") == "SEARCH"

    def test_dispatch_discards_synthetic_step(self):
        """dispatch binds `has_context, _ = ...`; synthetic SEARCH dict is unused."""
        st = _code_state(ranked_context=[])
        called = []

        def cap(q, s):
            called.append(q)
            return _valid_hit()

        step = {"id": 1, "action": "EXPLAIN", "description": "explain replanner flow"}
        with patch("agent.execution.step_dispatcher._search_fn", side_effect=cap):
            with patch(
                "agent.execution.step_dispatcher.run_retrieval_pipeline",
                side_effect=_inject_pipeline_sets_substantive_ranked,
            ):
                with patch("agent.execution.step_dispatcher.call_reasoning_model", return_value="x" * 50):
                    with patch("agent.execution.step_dispatcher.call_small_model", return_value="x" * 50):
                        with patch("agent.execution.step_dispatcher.get_model_for_task") as gmt:
                            gmt.return_value = MagicMock(value="SMALL")
                            dispatch(step, st)
        assert called  # inject ran; synthetic step was not executed as a separate SEARCH


class TestShapeQueryNarrowAndNone:
    """`_shape_query_for_explain_retrieval` — narrow token vs None → dispatch uses `or base`."""

    def test_narrow_token(self):
        assert _shape_query_for_explain_retrieval("explain dispatcher flow") == "dispatcher"

    def test_shaped_equals_description_when_single_token(self):
        assert _shape_query_for_explain_retrieval("explain foo") == "foo"


class TestExplainInjectDispatchIntegration:
    """`dispatch` EXPLAIN + code lane + empty `ranked_context`."""

    def test_ranked_context_skips_search_and_pipeline(self):
        st = _code_state(
            ranked_context=[{"file": "z.py", "snippet": "def ctx():\n    return 0\n"}],
        )
        with patch("agent.execution.step_dispatcher._search_fn") as sf:
            with patch("agent.execution.step_dispatcher.run_retrieval_pipeline") as rpp:
                with patch("agent.execution.step_dispatcher.call_reasoning_model", return_value="y" * 45):
                    with patch("agent.execution.step_dispatcher.get_model_for_task") as gmt:
                        gmt.return_value = MagicMock(value="REASONING")
                        dispatch(
                            {"id": 1, "action": "EXPLAIN", "description": "what is z"},
                            st,
                        )
        sf.assert_not_called()
        rpp.assert_not_called()

    def test_query_key_used_directly(self):
        st = _code_state(ranked_context=[])
        got = []

        def cap(q, s):
            got.append(q)
            return _valid_hit()

        with patch("agent.execution.step_dispatcher._search_fn", side_effect=cap):
            with patch(
                "agent.execution.step_dispatcher.run_retrieval_pipeline",
                side_effect=_inject_pipeline_sets_substantive_ranked,
            ):
                with patch("agent.execution.step_dispatcher.call_reasoning_model", return_value="y" * 45):
                    with patch("agent.execution.step_dispatcher.get_model_for_task") as gmt:
                        gmt.return_value = MagicMock(value="REASONING")
                        dispatch(
                            {
                                "id": 1,
                                "action": "EXPLAIN",
                                "description": "long natural language explain dispatcher routing",
                                "query": "explicit_query_token",
                            },
                            st,
                        )
        assert got == ["explicit_query_token"]

    def test_blank_query_falls_through_to_description_shaping(self):
        """`step.get('query')` is '' → falsy; shaping applies to description."""
        st = _code_state(ranked_context=[])
        got = []

        def cap(q, s):
            got.append(q)
            return _valid_hit()

        with patch("agent.execution.step_dispatcher._search_fn", side_effect=cap):
            with patch(
                "agent.execution.step_dispatcher.run_retrieval_pipeline",
                side_effect=_inject_pipeline_sets_substantive_ranked,
            ):
                with patch("agent.execution.step_dispatcher.call_reasoning_model", return_value="y" * 45):
                    with patch("agent.execution.step_dispatcher.get_model_for_task") as gmt:
                        gmt.return_value = MagicMock(value="REASONING")
                        dispatch(
                            {
                                "id": 1,
                                "action": "EXPLAIN",
                                "description": "explain replanner flow",
                                "query": "",
                            },
                            st,
                        )
        assert got == ["replanner"]

    def test_blank_description_search_uses_empty_query_string(self):
        """`description` and `query` absent/empty → `base` is ''; shaping returns None; `_search_fn` sees ''."""
        st = _code_state(ranked_context=[])
        got = []

        def cap(q, s):
            got.append(q)
            return _valid_hit()

        with patch("agent.execution.step_dispatcher._search_fn", side_effect=cap):
            with patch(
                "agent.execution.step_dispatcher.run_retrieval_pipeline",
                side_effect=_inject_pipeline_sets_substantive_ranked,
            ):
                with patch("agent.execution.step_dispatcher.call_reasoning_model", return_value="y" * 45):
                    with patch("agent.execution.step_dispatcher.get_model_for_task") as gmt:
                        gmt.return_value = MagicMock(value="REASONING")
                        dispatch({"id": 1, "action": "EXPLAIN", "description": ""}, st)
        assert got == [""]

    def test_whitespace_only_query_is_truthy_skips_shaping(self):
        """Non-empty whitespace `query` is truthy; base becomes that string (no shaping)."""
        st = _code_state(ranked_context=[])
        got = []

        def cap(q, s):
            got.append(q)
            return _valid_hit()

        with patch("agent.execution.step_dispatcher._search_fn", side_effect=cap):
            with patch(
                "agent.execution.step_dispatcher.run_retrieval_pipeline",
                side_effect=_inject_pipeline_sets_substantive_ranked,
            ):
                with patch("agent.execution.step_dispatcher.call_reasoning_model", return_value="y" * 45):
                    with patch("agent.execution.step_dispatcher.get_model_for_task") as gmt:
                        gmt.return_value = MagicMock(value="REASONING")
                        dispatch(
                            {
                                "id": 1,
                                "action": "EXPLAIN",
                                "description": "explain replanner flow",
                                "query": "   ",
                            },
                            st,
                        )
        assert got == ["   "]

    def test_exactly_one_search_fn_call(self):
        st = _code_state(ranked_context=[])
        with patch("agent.execution.step_dispatcher._search_fn", return_value=_valid_hit()) as sf:
            with patch(
                "agent.execution.step_dispatcher.run_retrieval_pipeline",
                side_effect=_inject_pipeline_sets_substantive_ranked,
            ):
                with patch("agent.execution.step_dispatcher.call_reasoning_model", return_value="y" * 45):
                    with patch("agent.execution.step_dispatcher.get_model_for_task") as gmt:
                        gmt.return_value = MagicMock(value="REASONING")
                        dispatch({"id": 1, "action": "EXPLAIN", "description": "explain foo"}, st)
        assert sf.call_count == 1

    def test_run_retrieval_pipeline_called_on_valid_hit(self):
        st = _code_state(ranked_context=[])
        with patch("agent.execution.step_dispatcher._search_fn", return_value=_valid_hit()):
            with patch(
                "agent.execution.step_dispatcher.run_retrieval_pipeline",
                side_effect=_inject_pipeline_sets_substantive_ranked,
            ) as rpp:
                with patch("agent.execution.step_dispatcher.call_reasoning_model", return_value="y" * 45):
                    with patch("agent.execution.step_dispatcher.get_model_for_task") as gmt:
                        gmt.return_value = MagicMock(value="REASONING")
                        dispatch({"id": 1, "action": "EXPLAIN", "description": "explain foo"}, st)
        rpp.assert_called_once()

    def test_invalid_hit_retryable_failure(self):
        st = _code_state(ranked_context=[])
        bad = {"results": [], "query": "q"}
        with patch("agent.execution.step_dispatcher._search_fn", return_value=bad):
            out = dispatch({"id": 1, "action": "EXPLAIN", "description": "explain foo"}, st)
        assert out["success"] is False
        assert "No context for EXPLAIN" in (out.get("error") or "")
        assert out.get("classification") == "RETRYABLE_FAILURE"

    def test_file_search_marker_rejected(self):
        st = _code_state(ranked_context=[])
        raw = {
            "results": [{"file": "x.py", "snippet": "a"}],
            "retrieval_fallback": "file_search",
            "query": "q",
        }
        with patch("agent.execution.step_dispatcher._search_fn", return_value=raw):
            out = dispatch({"id": 1, "action": "EXPLAIN", "description": "explain foo"}, st)
        assert out["success"] is False

    def test_list_dir_marker_rejected(self):
        st = _code_state(ranked_context=[])
        raw = {
            "results": [{"file": "x.py", "snippet": "a"}],
            "retrieval_fallback": "list_dir",
            "query": "q",
        }
        with patch("agent.execution.step_dispatcher._search_fn", return_value=raw):
            out = dispatch({"id": 1, "action": "EXPLAIN", "description": "explain foo"}, st)
        assert out["success"] is False

    def test_results_nonempty_but_invalid_first_row(self):
        """`[{'snippet':'x'}]` — no file → invalid."""
        st = _code_state(ranked_context=[])
        raw = {"results": [{"snippet": "only"}], "query": "q"}
        with patch("agent.execution.step_dispatcher._search_fn", return_value=raw):
            out = dispatch({"id": 1, "action": "EXPLAIN", "description": "explain foo"}, st)
        assert out["success"] is False

    def test_malformed_non_py_empty_snippet_rejected(self):
        st = _code_state(ranked_context=[])
        raw = {"results": [{"file": "README.md", "snippet": ""}], "query": "q"}
        assert not _is_valid_search_result(raw["results"], raw)
        with patch("agent.execution.step_dispatcher._search_fn", return_value=raw):
            out = dispatch({"id": 1, "action": "EXPLAIN", "description": "explain foo"}, st)
        assert out["success"] is False

    def test_inject_does_not_set_policy_search_fields(self):
        """`_execute_search` sets `search_query_rewritten` / policy memory; inject does not."""
        st = _code_state(ranked_context=[])
        with patch("agent.execution.step_dispatcher._search_fn", return_value=_valid_hit()):
            with patch(
                "agent.execution.step_dispatcher.run_retrieval_pipeline",
                side_effect=_inject_pipeline_sets_substantive_ranked,
            ):
                with patch("agent.execution.step_dispatcher.call_reasoning_model", return_value="y" * 45):
                    with patch("agent.execution.step_dispatcher.get_model_for_task") as gmt:
                        gmt.return_value = MagicMock(value="REASONING")
                        dispatch({"id": 1, "action": "EXPLAIN", "description": "explain foo"}, st)
        assert st.context.get("search_query_rewritten") is None
        assert st.context.get("search_results") is None

    def test_success_reaches_model_with_context_block(self):
        st = _code_state(ranked_context=[])

        def fake_pipeline(results, state, query):
            state.context["ranked_context"] = [
                {"file": "a.py", "snippet": "def ctx_body():\n    return 1\n"},
            ]

        with patch("agent.execution.step_dispatcher._search_fn", return_value=_valid_hit()):
            with patch(
                "agent.execution.step_dispatcher.run_retrieval_pipeline",
                side_effect=fake_pipeline,
            ):
                with patch("agent.execution.step_dispatcher.call_reasoning_model") as crm:
                    with patch("agent.execution.step_dispatcher.get_model_for_task") as gmt:
                        gmt.return_value = MagicMock(value="REASONING")
                        dispatch({"id": 1, "action": "EXPLAIN", "description": "explain foo"}, st)
        assert crm.called
        prompt = crm.call_args[0][0]
        assert "Question:" in prompt
        assert "BEGIN CONTEXT" in prompt or "body" in prompt


class TestExplainInjectAsymmetryVsSearch:
    """Explicit current asymmetry (not prescribing future design)."""

    def test_execute_with_policy_not_called(self):
        st = _code_state(ranked_context=[])
        pe = MagicMock(spec=ExecutionPolicyEngine)
        pe.execute_with_policy = MagicMock(
            return_value={"success": True, "output": _valid_hit(), "classification": "SUCCESS"}
        )
        with patch("agent.execution.step_dispatcher._policy_engine", pe):
            with patch("agent.execution.step_dispatcher._search_fn", return_value=_valid_hit()):
                with patch(
                    "agent.execution.step_dispatcher.run_retrieval_pipeline",
                    side_effect=_inject_pipeline_sets_substantive_ranked,
                ):
                    with patch("agent.execution.step_dispatcher.call_reasoning_model", return_value="y" * 45):
                        with patch("agent.execution.step_dispatcher.get_model_for_task") as gmt:
                            gmt.return_value = MagicMock(value="REASONING")
                            dispatch({"id": 1, "action": "EXPLAIN", "description": "explain foo"}, st)
        pe.execute_with_policy.assert_not_called()

    def test_rewrite_query_with_context_not_called(self):
        st = _code_state(ranked_context=[])
        with patch("agent.execution.step_dispatcher.rewrite_query_with_context") as rw:
            with patch("agent.execution.step_dispatcher._search_fn", return_value=_valid_hit()):
                with patch(
                    "agent.execution.step_dispatcher.run_retrieval_pipeline",
                    side_effect=_inject_pipeline_sets_substantive_ranked,
                ):
                    with patch("agent.execution.step_dispatcher.call_reasoning_model", return_value="y" * 45):
                        with patch("agent.execution.step_dispatcher.get_model_for_task") as gmt:
                            gmt.return_value = MagicMock(value="REASONING")
                            dispatch({"id": 1, "action": "EXPLAIN", "description": "explain foo"}, st)
        rw.assert_not_called()

    def test_get_initial_search_variants_not_called(self):
        st = _code_state(ranked_context=[])
        with patch("agent.execution.mutation_strategies.get_initial_search_variants") as gv:
            gv.return_value = ["a", "b"]
            with patch("agent.execution.step_dispatcher._search_fn", return_value=_valid_hit()):
                with patch(
                    "agent.execution.step_dispatcher.run_retrieval_pipeline",
                    side_effect=_inject_pipeline_sets_substantive_ranked,
                ):
                    with patch("agent.execution.step_dispatcher.call_reasoning_model", return_value="y" * 45):
                        with patch("agent.execution.step_dispatcher.get_model_for_task") as gmt:
                            gmt.return_value = MagicMock(value="REASONING")
                            dispatch({"id": 1, "action": "EXPLAIN", "description": "explain foo"}, st)
        gv.assert_not_called()

    def test_single_shot_where_search_would_retry(self):
        """First raw empty, second would succeed: inject still fails (one `_search_fn` call)."""
        st = _code_state(ranked_context=[])
        calls = {"n": 0}

        def seq(q, s):
            calls["n"] += 1
            if calls["n"] == 1:
                return {"results": [], "query": q}
            return _valid_hit()

        with patch("agent.execution.step_dispatcher._search_fn", side_effect=seq):
            out = dispatch({"id": 1, "action": "EXPLAIN", "description": "explain foo"}, st)
        assert out["success"] is False
        assert calls["n"] == 1

    def test_shared_validity_gate_symbol(self):
        """Inject imports `_is_valid_search_result` from policy_engine — same as SEARCH policy."""
        import agent.execution.step_dispatcher as sd

        assert sd._is_valid_search_result is _is_valid_search_result


class TestExplainGateGroundingReady:
    """`code_explain_grounding_ready` requires ranked_context substance or typed metadata."""

    def test_empty_ranked_not_ready(self):
        from agent.execution.explain_gate import code_explain_grounding_ready

        r, sig = code_explain_grounding_ready(
            {"artifact_mode": "code"}, _code_state(ranked_context=[])
        )
        assert r is False
        assert sig.get("signal") == "empty_ranked_context"

    def test_blocked_path_when_not_ready(self):
        st = _code_state(
            ranked_context=[{"file": "a.py", "snippet": "def f():\n    pass\n"}],
        )
        with patch(
            "agent.execution.step_dispatcher.code_explain_grounding_ready",
            return_value=(False, {"reason_code": "insufficient_grounding"}),
        ):
            out = dispatch({"id": 1, "action": "EXPLAIN", "description": "explain"}, st)
        assert out.get("success") is True
        assert "blocked" in (out.get("error") or "").lower()
        assert out.get("classification") == "SUCCESS"


class TestPipelineEdgeCases:
    """`run_retrieval_pipeline` no-op: grounding blocks EXPLAIN before LLM."""

    def test_pipeline_noop_blocks_explain_grounding(self):
        """If pipeline leaves `ranked_context` empty, grounding_ready fails (no silent no-context EXPLAIN)."""
        st = _code_state(ranked_context=[])
        with patch("agent.execution.step_dispatcher._search_fn", return_value=_valid_hit()):
            with patch("agent.execution.step_dispatcher.run_retrieval_pipeline", lambda *a, **k: None):
                with patch("agent.execution.step_dispatcher.call_reasoning_model") as crm:
                    with patch("agent.execution.step_dispatcher.get_model_for_task") as gmt:
                        gmt.return_value = MagicMock(value="REASONING")
                        out = dispatch({"id": 1, "action": "EXPLAIN", "description": "explain foo"}, st)
        crm.assert_not_called()
        assert out.get("success") is True
        assert "grounding" in (out.get("error") or "").lower()


class TestValidatorBoundaryStrings:
    """`EXPLAIN_NEEDS_CONTEXT_PREFIX` aligns with validator substring (registry may vary)."""

    def test_prefix_constant_matches_validator_expectation(self):
        from agent.orchestrator.validator import _validate_step_rules
        from agent.memory.step_result import StepResult

        step = {"id": 1, "action": "EXPLAIN", "description": "x"}
        result = StepResult(
            step_id=1,
            action="EXPLAIN",
            success=True,
            output=EXPLAIN_NEEDS_CONTEXT_PREFIX + " — please search.",
            latency_seconds=0.0,
        )
        valid, feedback = _validate_step_rules(step, result)
        assert valid is False
        assert "empty context" in feedback.lower()
