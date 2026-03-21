"""Unit tests for ExecutionPolicyEngine: retry and mutation for SEARCH."""

import unittest
from unittest.mock import MagicMock, patch

from agent.execution.mutation_strategies import generate_query_variants, get_initial_search_variants
from agent.execution.policy_engine import (
    ExecutionPolicyEngine,
    InvalidStepError,
    POLICIES,
    _MAX_REWRITE_QUERIES_PER_SEARCH_ATTEMPT,
    _is_valid_search_result,
    _normalize_rewrite_query_list,
    search_result_quality,
    validate_step_input,
)
from agent.retrieval.result_contract import RETRIEVAL_RESULT_TYPE_SYMBOL_BODY
from agent.memory.state import AgentState


class TestStage44RewriteQueryCap(unittest.TestCase):
    """Stage 44: rewriter query lists capped, deduped, stripped on attempts 2+."""

    def test_normalize_rewrite_query_list_strip_dedupe_order_cap(self):
        raw = ["  a  ", "", "b", "a", "c", "d", "e", "f", "g", "h"]
        out = _normalize_rewrite_query_list(raw, _MAX_REWRITE_QUERIES_PER_SEARCH_ATTEMPT)
        self.assertEqual(out, ["a", "b", "c", "d", "e"])
        self.assertEqual(_MAX_REWRITE_QUERIES_PER_SEARCH_ATTEMPT, 5)

    def test_normalize_skips_non_str(self):
        self.assertEqual(_normalize_rewrite_query_list(["x", 99, None, "y"], 10), ["x", "y"])

    def test_rewrite_list_capped_before_search_fn(self):
        """Rewriter emits 8 distinct queries; only first 5 are executed per rewrite attempt."""
        search_queries = []

        def mock_search(query: str, state=None):
            search_queries.append(query)
            return {"results": [], "query": query}

        letters = [f"q{i}" for i in range(8)]

        def mock_rewrite(description: str, user_request: str, attempt_history: list, state=None) -> list:
            return letters

        with patch("agent.execution.policy_engine.get_initial_search_variants", return_value=["only"]):
            engine = ExecutionPolicyEngine(
                search_fn=mock_search,
                edit_fn=MagicMock(),
                infra_fn=MagicMock(),
                rewrite_query_fn=mock_rewrite,
                max_total_attempts=2,
            )
            step = {"id": 1, "action": "SEARCH", "description": "find"}
            state = AgentState(instruction="t", current_plan={"plan_id": "p", "steps": [step]})
            engine.execute_with_policy(step, state)

        self.assertEqual(search_queries[0], "only")
        self.assertEqual(search_queries[1:6], ["q0", "q1", "q2", "q3", "q4"])
        self.assertEqual(len(search_queries), 6)
        self.assertNotIn("q5", search_queries)

    def test_order_preserved_for_surviving_queries(self):
        self.assertEqual(
            _normalize_rewrite_query_list(["z", "a", "b", "z"], 10),
            ["z", "a", "b"],
        )

    def test_attempt1_deterministic_unchanged_rewriter_not_called(self):
        rewrite_calls = []

        def mock_rewrite(description: str, user_request: str, attempt_history: list, state=None) -> list:
            rewrite_calls.append(1)
            return ["q0"] * 20

        search_calls = []

        def mock_search(query: str, state=None):
            search_calls.append(query)
            return {"results": [{"file": "a.py", "snippet": "x"}], "query": query}

        with patch(
            "agent.execution.policy_engine.get_initial_search_variants",
            return_value=["first", "second"],
        ):
            engine = ExecutionPolicyEngine(
                search_fn=mock_search,
                edit_fn=MagicMock(),
                infra_fn=MagicMock(),
                rewrite_query_fn=mock_rewrite,
                max_total_attempts=5,
            )
            step = {"id": 1, "action": "SEARCH", "description": "x"}
            state = AgentState(instruction="t", current_plan={"plan_id": "p", "steps": [step]})
            r = engine.execute_with_policy(step, state)

        self.assertTrue(r["success"])
        self.assertEqual(rewrite_calls, [])
        self.assertEqual(search_calls, ["first"])

    def test_empty_after_normalize_falls_back_to_retrieval_input(self):
        calls = []

        def mock_search(query: str, state=None):
            calls.append(query)
            if query == "fallback_desc":
                return {"results": [{"file": "ok.py", "snippet": "1"}], "query": query}
            return {"results": [], "query": query}

        def mock_rewrite(description: str, user_request: str, attempt_history: list, state=None) -> list:
            return ["", "   ", "\t"]

        with patch("agent.execution.policy_engine.get_initial_search_variants", return_value=["only"]):
            engine = ExecutionPolicyEngine(
                search_fn=mock_search,
                edit_fn=MagicMock(),
                infra_fn=MagicMock(),
                rewrite_query_fn=mock_rewrite,
                max_total_attempts=5,
            )
            step = {"id": 1, "action": "SEARCH", "description": "fallback_desc"}
            state = AgentState(instruction="t", current_plan={"plan_id": "p", "steps": [step]})
            r = engine.execute_with_policy(step, state)

        self.assertTrue(r["success"])
        self.assertEqual(calls[0], "only")
        self.assertEqual(calls[1], "fallback_desc")


class TestStage43FileSearchHonesty(unittest.TestCase):
    """Stage 43: file_search directory-listing fallback is not valid retrieval success."""

    def test_is_valid_search_result_false_when_retrieval_fallback_file_search(self):
        results = [{"file": "/proj/agent/foo.py", "snippet": "foo.py"}]
        raw = {"results": results, "retrieval_fallback": "file_search", "query": "x"}
        self.assertFalse(_is_valid_search_result(results, raw))

    def test_is_valid_search_result_true_same_shape_without_fallback(self):
        results = [{"file": "/proj/agent/foo.py", "snippet": "foo.py"}]
        self.assertTrue(_is_valid_search_result(results, None))
        self.assertTrue(_is_valid_search_result(results, {"results": results, "query": "x"}))

    def test_search_file_search_on_attempt1_does_not_stop_then_rewriter_succeeds(self):
        calls = []

        def mock_search(query: str, state=None):
            calls.append(query)
            if len(calls) == 1:
                return {
                    "results": [{"file": "/x/a.py", "snippet": "a.py"}],
                    "retrieval_fallback": "file_search",
                    "query": query,
                }
            return {"results": [{"file": "/y/ok.py", "snippet": "real hit"}], "query": query}

        def mock_rewrite(description: str, user_request: str, attempt_history: list, state=None) -> str:
            return "rewritten_query"

        with patch("agent.execution.policy_engine.get_initial_search_variants", return_value=["q"]):
            engine = ExecutionPolicyEngine(
                search_fn=mock_search,
                edit_fn=MagicMock(),
                infra_fn=MagicMock(),
                rewrite_query_fn=mock_rewrite,
                max_total_attempts=5,
            )
            step = {"id": 1, "action": "SEARCH", "description": "find x"}
            state = AgentState(instruction="test", current_plan={"plan_id": "p", "steps": [step]})
            result = engine.execute_with_policy(step, state)

        self.assertTrue(result["success"])
        self.assertEqual(calls, ["q", "rewritten_query"])


class TestStage45ListDirHonesty(unittest.TestCase):
    """Stage 45: list_dir directory entries are not valid semantic SEARCH success."""

    def test_is_valid_search_result_false_when_retrieval_fallback_list_dir(self):
        results = [{"file": "/proj/src/x.py", "snippet": "x.py"}]
        raw = {"results": results, "retrieval_fallback": "list_dir", "query": "src"}
        self.assertFalse(_is_valid_search_result(results, raw))

    def test_is_valid_search_result_true_same_shape_without_list_dir_marker(self):
        results = [{"file": "/proj/src/x.py", "snippet": "x.py"}]
        self.assertTrue(_is_valid_search_result(results, None))
        self.assertTrue(_is_valid_search_result(results, {"results": results, "query": "src"}))

    def test_search_list_dir_on_attempt1_does_not_stop_then_rewriter_succeeds(self):
        calls = []

        def mock_search(query: str, state=None):
            calls.append(query)
            if len(calls) == 1:
                return {
                    "results": [{"file": "/proj/a/foo.py", "snippet": "foo.py"}],
                    "retrieval_fallback": "list_dir",
                    "query": query,
                }
            return {"results": [{"file": "/y/ok.py", "snippet": "real hit"}], "query": query}

        def mock_rewrite(description: str, user_request: str, attempt_history: list, state=None) -> str:
            return "rewritten_query"

        with patch("agent.execution.policy_engine.get_initial_search_variants", return_value=["q"]):
            engine = ExecutionPolicyEngine(
                search_fn=mock_search,
                edit_fn=MagicMock(),
                infra_fn=MagicMock(),
                rewrite_query_fn=mock_rewrite,
                max_total_attempts=5,
            )
            step = {"id": 1, "action": "SEARCH", "description": "find x"}
            state = AgentState(instruction="test", current_plan={"plan_id": "p", "steps": [step]})
            result = engine.execute_with_policy(step, state)

        self.assertTrue(result["success"])
        self.assertEqual(calls, ["q", "rewritten_query"])


class TestGenerateQueryVariants(unittest.TestCase):
    """Test Phase 1 identifier variants."""

    def test_router_eval2_produces_expected_variants(self):
        variants = generate_query_variants("router eval2")
        self.assertIn("router_eval_v2", variants)
        self.assertIn("router_eval2", variants)
        self.assertIn("router_eval", variants)
        self.assertIn("router", variants)

    def test_empty_string_returns_empty_list(self):
        self.assertEqual(generate_query_variants(""), [])
        self.assertEqual(generate_query_variants("   "), [])


class TestGetInitialSearchVariants(unittest.TestCase):
    """Stage 42: get_initial_search_variants."""

    def test_returns_base_first_and_cap_three(self):
        out = get_initial_search_variants("step executor", max_total=3)
        self.assertGreaterEqual(len(out), 1)
        self.assertEqual(out[0], "step executor")
        self.assertLessEqual(len(out), 3)

    def test_hard_cap(self):
        out = get_initial_search_variants("router eval2", max_total=3)
        self.assertLessEqual(len(out), 3)

    def test_dedupe_no_duplicate_strings(self):
        out = get_initial_search_variants("router eval2", max_total=3)
        self.assertEqual(len(out), len(set(out)))

    def test_empty_base_returns_empty(self):
        self.assertEqual(get_initial_search_variants(""), [])
        self.assertEqual(get_initial_search_variants("   "), [])

    def test_max_total_one_returns_only_base(self):
        out = get_initial_search_variants("foo bar", max_total=1)
        self.assertEqual(out, ["foo bar"])

    def test_whitespace_only_base_returns_empty(self):
        self.assertEqual(get_initial_search_variants("  \n\t  "), [])


def _identity_rewrite(description: str, user_request: str, attempt_history: list, state=None) -> str:
    """Rewriter that passes through description (new context-aware signature)."""
    return (description or "").strip() or description


class TestExecutionPolicyEngineSearch(unittest.TestCase):
    """Policy engine SEARCH: retries with context-aware rewrite until success or exhausted."""

    def test_search_retries_then_succeeds_on_third_query(self):
        # Stage 42: attempt 1 tries up to 3 deterministic variants without rewriter.
        # First two searches empty, third succeeds (same third-query success as before).
        call_count = 0

        def mock_search(query: str, state=None):
            nonlocal call_count
            call_count += 1
            if call_count <= 2:
                return {"results": [], "query": query}
            return {"results": [{"file": "x.py", "snippet": "y"}], "query": query}

        rewrite_calls = []

        def mock_rewrite(description: str, user_request: str, attempt_history: list, state=None) -> str:
            rewrite_calls.append(len(attempt_history))
            n = len(attempt_history)
            if n == 0:
                return "router_eval2"
            if n == 1:
                return "router_eval_v2"
            return "router_eval"

        engine = ExecutionPolicyEngine(
            search_fn=mock_search,
            edit_fn=MagicMock(return_value={"success": True, "output": {}}),
            infra_fn=MagicMock(return_value={"success": True, "output": {"returncode": 0}}),
            rewrite_query_fn=mock_rewrite,
            max_total_attempts=10,
        )
        step = {"id": 1, "action": "SEARCH", "description": "router eval2"}
        state = AgentState(instruction="test", current_plan={"plan_id": "policy_plan", "steps": [step]})

        result = engine.execute_with_policy(step, state)

        self.assertTrue(result["success"], result)
        self.assertIn("output", result)
        out = result["output"]
        self.assertIn("results", out)
        self.assertEqual(len(out["results"]), 1)
        self.assertIn("attempt_history", out)
        history = out["attempt_history"]
        self.assertGreaterEqual(len(history), 3, "should have at least 3 tries")
        self.assertTrue(any(h.get("result_count", 0) > 0 for h in history), history)
        self.assertEqual(call_count, len(history))
        self.assertEqual(rewrite_calls, [], "attempt 1 must not call rewriter when deterministic variants run")

    def test_search_uses_query_when_present_over_description(self):
        """SEARCH retrieval uses step.query when present; falls back to description when absent."""
        search_queries = []

        def mock_search(query: str, state=None):
            search_queries.append(query)
            return {"results": [{"file": "x.py", "snippet": "y"}], "query": query}

        engine = ExecutionPolicyEngine(
            search_fn=mock_search,
            edit_fn=MagicMock(),
            infra_fn=MagicMock(),
            rewrite_query_fn=_identity_rewrite,
            max_total_attempts=3,
        )
        step = {
            "id": 1,
            "action": "SEARCH",
            "query": "explicit_search_query",
            "description": "Find where the Step Executor class is",
        }
        state = AgentState(instruction="test", current_plan={"plan_id": "p", "steps": [step]})
        result = engine.execute_with_policy(step, state)

        self.assertTrue(result["success"])
        self.assertEqual(search_queries[0], "explicit_search_query")

    def test_search_rewriter_receives_planner_step_user_request_and_attempt_history(self):
        """After Stage 42 attempt 1 (deterministic variants), rewriter runs with full context."""
        rewrite_calls = []

        def mock_rewrite(description: str, user_request: str, attempt_history: list, state=None) -> str:
            rewrite_calls.append({
                "description": description,
                "user_request": user_request,
                "attempt_history_len": len(attempt_history),
            })
            n = len(rewrite_calls)
            return f"StepExecutor_v{n}"

        search_count = 0

        def mock_search(query: str, state=None):
            nonlocal search_count
            search_count += 1
            if search_count >= 6:
                return {"results": [{"file": "executor.py", "snippet": "class StepExecutor"}], "query": query}
            return {"results": [], "query": query}

        engine = ExecutionPolicyEngine(
            search_fn=mock_search,
            edit_fn=MagicMock(),
            infra_fn=MagicMock(),
            rewrite_query_fn=mock_rewrite,
            max_total_attempts=5,
        )
        step = {"id": 1, "action": "SEARCH", "description": "Find where the Step Executor class is"}
        state = AgentState(
            instruction="Find where the Step Executor class is",
            current_plan={"plan_id": "policy_rewriter_plan", "steps": [step]},
        )

        result = engine.execute_with_policy(step, state)

        self.assertTrue(result["success"], result)
        self.assertEqual(len(rewrite_calls), 3, "rewriter runs only after attempt 1 variants (3 calls)")
        self.assertEqual(rewrite_calls[0]["description"], "Find where the Step Executor class is")
        self.assertEqual(rewrite_calls[0]["user_request"], "Find where the Step Executor class is")
        self.assertEqual(rewrite_calls[0]["attempt_history_len"], 3, "first rewriter call sees 3 failed variant tries")
        self.assertEqual(rewrite_calls[1]["attempt_history_len"], 4)
        self.assertEqual(rewrite_calls[2]["attempt_history_len"], 5)

    def test_attempt1_uses_deterministic_variants_no_rewriter_until_success(self):
        rewrite_calls = []

        def mock_rewrite(description: str, user_request: str, attempt_history: list, state=None) -> str:
            rewrite_calls.append(1)
            return "never_first"

        call_count = 0

        def mock_search(query: str, state=None):
            nonlocal call_count
            call_count += 1
            if call_count <= 2:
                return {"results": [], "query": query}
            return {"results": [{"file": "x.py", "snippet": "y"}], "query": query}

        engine = ExecutionPolicyEngine(
            search_fn=mock_search,
            edit_fn=MagicMock(),
            infra_fn=MagicMock(),
            rewrite_query_fn=mock_rewrite,
            max_total_attempts=5,
        )
        step = {"id": 1, "action": "SEARCH", "description": "router eval2"}
        state = AgentState(instruction="test", current_plan={"plan_id": "p", "steps": [step]})
        result = engine.execute_with_policy(step, state)
        self.assertTrue(result["success"])
        self.assertEqual(rewrite_calls, [])
        self.assertEqual(call_count, 3)

    def test_attempt1_first_variant_succeeds_short_circuits(self):
        rewrite_calls = []

        def mock_rewrite(description: str, user_request: str, attempt_history: list, state=None) -> str:
            rewrite_calls.append(1)
            return "x"

        search_calls = []

        def mock_search(query: str, state=None):
            search_calls.append(query)
            return {"results": [{"file": "a.py", "snippet": "ok"}], "query": query}

        with patch(
            "agent.execution.policy_engine.get_initial_search_variants",
            return_value=["a", "b", "c"],
        ):
            engine = ExecutionPolicyEngine(
                search_fn=mock_search,
                edit_fn=MagicMock(),
                infra_fn=MagicMock(),
                rewrite_query_fn=mock_rewrite,
                max_total_attempts=5,
            )
            step = {"id": 1, "action": "SEARCH", "description": "ignored"}
            state = AgentState(instruction="test", current_plan={"plan_id": "p", "steps": [step]})
            result = engine.execute_with_policy(step, state)

        self.assertTrue(result["success"])
        self.assertEqual(search_calls, ["a"])
        self.assertEqual(rewrite_calls, [])

    def test_attempt2_uses_rewriter_when_attempt1_variants_exhausted(self):
        rewrite_calls = []

        def mock_rewrite(description: str, user_request: str, attempt_history: list, state=None) -> str:
            rewrite_calls.append(len(attempt_history))
            return "rewritten_ok"

        def mock_search(query: str, state=None):
            if query == "rewritten_ok":
                return {"results": [{"file": "z.py", "snippet": "x"}], "query": query}
            return {"results": [], "query": query}

        engine = ExecutionPolicyEngine(
            search_fn=mock_search,
            edit_fn=MagicMock(),
            infra_fn=MagicMock(),
            rewrite_query_fn=mock_rewrite,
            max_total_attempts=5,
        )
        step = {"id": 1, "action": "SEARCH", "description": "router eval2"}
        state = AgentState(instruction="test", current_plan={"plan_id": "p", "steps": [step]})
        result = engine.execute_with_policy(step, state)
        self.assertTrue(result["success"])
        self.assertEqual(rewrite_calls, [3], "rewriter only after 3 failed deterministic tries")
        out = result["output"]
        self.assertTrue(any(r.get("file", "").endswith("z.py") for r in (out.get("results") or [])))

    def test_search_returns_failure_with_only_attempt_history_when_exhausted(self):
        def mock_search_empty(_query: str, state=None):
            return {"results": [], "query": _query}

        engine = ExecutionPolicyEngine(
            search_fn=mock_search_empty,
            edit_fn=MagicMock(),
            infra_fn=MagicMock(),
            rewrite_query_fn=_identity_rewrite,
            max_total_attempts=3,
        )
        step = {"id": 1, "action": "SEARCH", "description": "nonexistent symbol xyz"}
        state = AgentState(instruction="test", current_plan={"plan_id": "policy_exhausted_plan", "steps": [step]})

        result = engine.execute_with_policy(step, state)

        self.assertFalse(result["success"])
        self.assertIn("error", result)
        self.assertNotIn("results", result["output"])
        self.assertIn("attempt_history", result["output"])
        self.assertGreater(len(result["output"]["attempt_history"]), 0)


class TestValidateStepInput(unittest.TestCase):
    """Pre-dispatch schema validation."""

    def test_valid_search_step_passes(self):
        validate_step_input({"action": "SEARCH", "description": "find foo"})

    def test_valid_edit_step_passes(self):
        validate_step_input({"action": "EDIT", "description": "change bar"})

    def test_valid_explain_step_passes(self):
        validate_step_input({"action": "EXPLAIN", "description": "explain baz"})

    def test_valid_infra_step_passes(self):
        validate_step_input({"action": "INFRA", "description": ""})

    def test_valid_search_candidates_step_passes(self):
        validate_step_input({"action": "SEARCH_CANDIDATES", "query": "find foo", "description": "candidate discovery"})

    def test_valid_build_context_step_passes(self):
        validate_step_input({"action": "BUILD_CONTEXT", "description": "build context from candidates"})

    def test_invalid_action_raises(self):
        with self.assertRaises(InvalidStepError) as ctx:
            validate_step_input({"action": "UNKNOWN", "description": "x"})
        self.assertIn("action must be one of", str(ctx.exception))

    def test_non_dict_raises(self):
        with self.assertRaises(InvalidStepError) as ctx:
            validate_step_input([])
        self.assertIn("must be a dict", str(ctx.exception))

    def test_description_too_long_raises(self):
        with self.assertRaises(InvalidStepError) as ctx:
            validate_step_input({"action": "SEARCH", "description": "x" * 50_001})
        self.assertIn("exceeds max length", str(ctx.exception))


class TestSearchPolicyChosenToolAndBlankInput(unittest.TestCase):
    """SEARCH attempt_history tool field; blank retrieval string behavior."""

    def test_attempt_history_records_chosen_tool(self):
        calls = []

        def mock_search(query: str, state=None):
            calls.append(query)
            return {"results": [{"file": "a.py", "snippet": "x"}], "query": query}

        engine = ExecutionPolicyEngine(
            search_fn=mock_search,
            edit_fn=MagicMock(),
            infra_fn=MagicMock(),
            rewrite_query_fn=lambda *a, **k: "r",
            max_total_attempts=5,
        )
        step = {"id": 1, "action": "SEARCH", "description": "find"}
        state = AgentState(instruction="t", current_plan={"plan_id": "p", "steps": [step]})
        state.context["chosen_tool"] = "retrieve_grep"
        with patch("agent.execution.policy_engine.get_initial_search_variants", return_value=["q1"]):
            r = engine.execute_with_policy(step, state)
        self.assertTrue(r["success"])
        hist = r["output"].get("attempt_history") or []
        self.assertTrue(hist)
        self.assertEqual(hist[0].get("tool"), "retrieve_grep")

    def test_whitespace_only_description_empty_variants_then_rewriter(self):
        """Strip makes retrieval_input ''; no deterministic variants; rewriter supplies query."""

        def mock_search(query: str, state=None):
            if query == "from_rewriter":
                return {"results": [{"file": "a.py", "snippet": "x"}], "query": query}
            return {"results": [], "query": query}

        rewrite_calls = []

        def mock_rewrite(description: str, user_request: str, attempt_history: list, state=None) -> str:
            rewrite_calls.append(1)
            return "from_rewriter"

        engine = ExecutionPolicyEngine(
            search_fn=mock_search,
            edit_fn=MagicMock(),
            infra_fn=MagicMock(),
            rewrite_query_fn=mock_rewrite,
            max_total_attempts=5,
        )
        step = {"id": 1, "action": "SEARCH", "description": "   \t"}
        state = AgentState(instruction="t", current_plan={"plan_id": "p", "steps": [step]})
        r = engine.execute_with_policy(step, state)
        self.assertTrue(r["success"])
        self.assertEqual(rewrite_calls, [1])


class TestSearchRunOncePath(unittest.TestCase):
    """max_total_attempts=0 routes SEARCH through _run_once (differs from _execute_search)."""

    def test_zero_max_attempts_uses_single_loop(self):
        def mock_search(query: str, state=None):
            return {"results": [{"file": "a.py", "snippet": "x"}], "query": query}

        engine = ExecutionPolicyEngine(
            search_fn=mock_search,
            edit_fn=MagicMock(),
            infra_fn=MagicMock(),
            rewrite_query_fn=_identity_rewrite,
            max_total_attempts=0,
        )
        step = {"id": 1, "action": "SEARCH", "description": "x"}
        state = AgentState(instruction="t", current_plan={"plan_id": "p", "steps": [step]})
        r = engine.execute_with_policy(step, state)
        self.assertTrue(r.get("success"))
        self.assertEqual(r["output"].get("search_quality"), "strong")
        self.assertEqual(state.context.get("search_quality"), "strong")


class TestSearchResultQuality(unittest.TestCase):
    """Soft signal: strong vs weak (does not change SEARCH validity)."""

    def test_weak_empty_or_file_only(self):
        self.assertEqual(search_result_quality(None), "weak")
        self.assertEqual(search_result_quality({}), "weak")
        self.assertEqual(search_result_quality({"results": []}), "weak")
        self.assertEqual(
            search_result_quality({"results": [{"file": "a.py", "snippet": ""}]}),
            "weak",
        )
        self.assertEqual(
            search_result_quality({"results": [{"file": "a.py", "snippet": "(no snippet)"}]}),
            "weak",
        )
        self.assertEqual(
            search_result_quality({"results": [{"file": "a.py", "snippet": "[]"}]}),
            "weak",
        )

    def test_strong_impl_body_or_symbol_type_or_snippet(self):
        self.assertEqual(
            search_result_quality(
                {"results": [{"file": "a.py", "implementation_body_present": True}]}
            ),
            "strong",
        )
        self.assertEqual(
            search_result_quality(
                {
                    "results": [
                        {"file": "a.py", "retrieval_result_type": RETRIEVAL_RESULT_TYPE_SYMBOL_BODY}
                    ]
                }
            ),
            "strong",
        )
        self.assertEqual(
            search_result_quality({"results": [{"file": "a.py", "snippet": "def foo():\n  pass"}]}),
            "strong",
        )

    def test_search_success_sets_context_and_output_quality(self):
        def mock_search(query: str, state=None):
            return {
                "results": [{"file": "x.py", "snippet": ""}],
                "query": query,
            }

        engine = ExecutionPolicyEngine(
            search_fn=mock_search,
            edit_fn=MagicMock(),
            infra_fn=MagicMock(),
            rewrite_query_fn=_identity_rewrite,
            max_total_attempts=1,
        )
        step = {"id": 1, "action": "SEARCH", "description": "find"}
        state = AgentState(instruction="t", current_plan={"plan_id": "p", "steps": [step]})
        r = engine.execute_with_policy(step, state)
        self.assertTrue(r["success"])
        out = r["output"]
        self.assertEqual(out.get("search_quality"), "weak")
        self.assertEqual(state.context.get("search_quality"), "weak")


class TestPolicies(unittest.TestCase):
    """Policy table has expected structure."""

    def test_search_policy_has_retry_on_and_max_attempts(self):
        self.assertIn("SEARCH", POLICIES)
        p = POLICIES["SEARCH"]
        self.assertEqual(p["retry_on"], ["empty_results"])
        self.assertGreaterEqual(p["max_attempts"], 1)

    def test_edit_and_infra_policies_defined(self):
        self.assertIn("EDIT", POLICIES)
        self.assertIn("INFRA", POLICIES)
        self.assertIn("retry_on", POLICIES["EDIT"])
        self.assertIn("retry_on", POLICIES["INFRA"])


if __name__ == "__main__":
    unittest.main()
