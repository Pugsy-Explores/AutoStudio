"""Tests for query rewriter: context-aware rewrite and attempt history formatting."""

import unittest

from agent.retrieval.query_rewriter import (
    MAX_ATTEMPT_HISTORY_FOR_REWRITE,
    SearchAttempt,
    _format_attempts_for_prompt,
    rewrite_query,
    rewrite_query_with_context,
)


class TestFormatAttemptsForPrompt(unittest.TestCase):
    """Test formatting of previous attempts for the rewrite prompt."""

    def test_empty_attempts_returns_none_yet(self):
        self.assertIn("none yet", _format_attempts_for_prompt([]))

    def test_single_attempt_formatted_with_count(self):
        attempts: list[SearchAttempt] = [
            {"query": "StepExecutor", "result_count": 0, "result_summary": "0 results"},
        ]
        out = _format_attempts_for_prompt(attempts)
        self.assertIn("StepExecutor", out)
        self.assertIn("0 result", out)

    def test_history_limited_to_max(self):
        attempts: list[SearchAttempt] = [
            {"query": f"q{i}", "result_count": 0, "result_summary": "0 results"}
            for i in range(MAX_ATTEMPT_HISTORY_FOR_REWRITE + 2)
        ]
        out = _format_attempts_for_prompt(attempts)
        # Only last MAX_ATTEMPT_HISTORY_FOR_REWRITE should appear
        self.assertIn("q" + str(MAX_ATTEMPT_HISTORY_FOR_REWRITE + 1), out)
        self.assertNotIn("q0", out)

    def test_error_in_attempt_shown(self):
        attempts: list[SearchAttempt] = [
            {"query": "foo", "error": "timeout"},
        ]
        out = _format_attempts_for_prompt(attempts)
        self.assertIn("foo", out)
        self.assertIn("Error", out)
        self.assertIn("timeout", out)


class TestRewriteQueryWithContext(unittest.TestCase):
    """Test context-aware rewrite (heuristic path, no LLM)."""

    def test_heuristic_rewrite_with_context_no_llm(self):
        out = rewrite_query_with_context(
            planner_step="Find where the Step Executor class is",
            user_request="Find where the Step Executor class is",
            previous_attempts=[],
            use_llm=False,
        )
        self.assertIsInstance(out, str)
        self.assertTrue(len(out) > 0)
        # Heuristic should strip filler and produce token-like string
        self.assertNotIn("find", out.lower())
        self.assertNotIn("where", out.lower())

    def test_heuristic_rewrite_with_previous_attempts_still_returns_query(self):
        previous: list[SearchAttempt] = [
            {"query": "step executor", "result_count": 0, "result_summary": "0 results"},
        ]
        out = rewrite_query_with_context(
            planner_step="Find the Step Executor class",
            user_request="Find where the Step Executor class is",
            previous_attempts=previous,
            use_llm=False,
        )
        self.assertIsInstance(out, str)
        self.assertTrue(len(out) > 0)


class TestRewriteQueryBackwardCompat(unittest.TestCase):
    """rewrite_query(text) still works without context."""

    def test_rewrite_query_heuristic(self):
        out = rewrite_query("Find the JWT token logic", use_llm=False)
        self.assertIsInstance(out, str)
        self.assertTrue(len(out) > 0)
