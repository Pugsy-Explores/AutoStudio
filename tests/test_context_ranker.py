"""Unit tests for context ranker and context pruner. No LLM calls — mock relevance scores."""

import unittest
from unittest.mock import patch

from agent.retrieval.context_pruner import prune_context
from agent.retrieval.context_ranker import (
    compute_filename_match,
    compute_reference_score,
    compute_symbol_match,
    rank_context,
)


class TestComputeSymbolMatch(unittest.TestCase):
    """Test symbol match helper."""

    def test_symbol_in_query_returns_one(self):
        self.assertEqual(compute_symbol_match("find StepExecutor", "StepExecutor"), 1.0)
        self.assertEqual(compute_symbol_match("StepExecutor class", "StepExecutor"), 1.0)

    def test_symbol_not_in_query_returns_zero(self):
        self.assertEqual(compute_symbol_match("find dispatch", "StepExecutor"), 0.0)

    def test_case_insensitive(self):
        self.assertEqual(compute_symbol_match("STEPEXECUTOR", "stepexecutor"), 1.0)

    def test_empty_returns_zero(self):
        self.assertEqual(compute_symbol_match("", "foo"), 0.0)
        self.assertEqual(compute_symbol_match("foo", ""), 0.0)


class TestComputeFilenameMatch(unittest.TestCase):
    """Test filename match helper."""

    def test_filename_in_query_returns_one(self):
        self.assertEqual(compute_filename_match("read executor.py", "agent/executor.py"), 1.0)

    def test_filename_not_in_query_returns_zero(self):
        self.assertEqual(compute_filename_match("read dispatch", "executor.py"), 0.0)


class TestComputeReferenceScore(unittest.TestCase):
    """Test reference score helper."""

    def test_reference_type_returns_half(self):
        self.assertEqual(compute_reference_score({"type": "reference"}), 0.5)

    def test_symbol_type_returns_zero(self):
        self.assertEqual(compute_reference_score({"type": "symbol"}), 0.0)

    def test_file_type_returns_zero(self):
        self.assertEqual(compute_reference_score({"type": "file"}), 0.0)


class TestRankContext(unittest.TestCase):
    """Test rank_context with mocked LLM."""

    @patch("agent.retrieval.context_ranker.call_reasoning_model")
    def test_ranking_sorts_candidates_correctly(self, mock_llm):
        """Ranking sorts by hybrid score descending. Batch returns one string with scores per line."""
        mock_llm.return_value = "0.3\n0.7\n0.5"  # batch: low, high, mid
        candidates = [
            {"file": "a.py", "symbol": "", "snippet": "snippet_a", "type": "file"},
            {"file": "b.py", "symbol": "", "snippet": "snippet_b", "type": "file"},
            {"file": "c.py", "symbol": "", "snippet": "snippet_c", "type": "file"},
        ]
        result = rank_context("query", candidates)
        self.assertEqual(len(result), 3)
        self.assertEqual(result[0]["file"], "b.py")
        self.assertEqual(result[1]["file"], "c.py")
        self.assertEqual(result[2]["file"], "a.py")

    @patch("agent.retrieval.context_ranker.call_reasoning_model")
    def test_symbol_matches_increase_score(self, mock_llm):
        """Candidate with symbol in query ranks higher when LLM scores equal."""
        mock_llm.return_value = "0.5\n0.5"  # batch: both equal
        candidates = [
            {"file": "x.py", "symbol": "dispatch", "snippet": "def dispatch", "type": "symbol"},
            {"file": "y.py", "symbol": "other", "snippet": "def other", "type": "symbol"},
        ]
        result = rank_context("find dispatch function", candidates)
        self.assertEqual(len(result), 2)
        self.assertEqual(result[0]["symbol"], "dispatch")

    @patch("agent.retrieval.context_ranker.call_reasoning_model")
    def test_diversity_penalty_prefers_different_files(self, mock_llm):
        """Same-file penalty: second snippet from same file ranks lower."""
        mock_llm.return_value = "0.6\n0.6\n0.5"  # a1, a2, b - a2 should drop below b due to penalty
        candidates = [
            {"file": "a.py", "symbol": "f1", "snippet": "def f1", "type": "symbol"},
            {"file": "a.py", "symbol": "f2", "snippet": "def f2", "type": "symbol"},
            {"file": "b.py", "symbol": "g", "snippet": "def g", "type": "symbol"},
        ]
        result = rank_context("query", candidates)
        self.assertEqual(len(result), 3)
        self.assertEqual(result[0]["file"], "a.py")
        self.assertEqual(result[1]["file"], "b.py")
        self.assertEqual(result[2]["file"], "a.py")


class TestPruneContext(unittest.TestCase):
    """Test context pruner."""

    def test_pruner_limits_snippets(self):
        ranked = [
            {"file": f"f{i}.py", "symbol": "", "snippet": "x" * 10, "type": "file"}
            for i in range(10)
        ]
        result = prune_context(ranked, max_snippets=3, max_chars=10000)
        self.assertEqual(len(result), 3)

    def test_pruner_limits_chars(self):
        ranked = [
            {"file": "a.py", "symbol": "", "snippet": "a" * 400, "type": "file"},
            {"file": "b.py", "symbol": "", "snippet": "b" * 400, "type": "file"},
            {"file": "c.py", "symbol": "", "snippet": "c" * 400, "type": "file"},
        ]
        result = prune_context(ranked, max_snippets=10, max_chars=500)
        self.assertLessEqual(sum(len(c.get("snippet", "")) for c in result), 500)
        self.assertLessEqual(len(result), 2)

    def test_deduplication_works(self):
        ranked = [
            {"file": "same.py", "symbol": "foo", "snippet": "s1", "type": "symbol"},
            {"file": "same.py", "symbol": "foo", "snippet": "s2", "type": "symbol"},
            {"file": "other.py", "symbol": "bar", "snippet": "s3", "type": "symbol"},
        ]
        result = prune_context(ranked, max_snippets=10, max_chars=10000)
        self.assertEqual(len(result), 2)
        seen = {(c["file"], c["symbol"]) for c in result}
        self.assertEqual(len(seen), 2)
        self.assertIn(("same.py", "foo"), seen)
        self.assertIn(("other.py", "bar"), seen)


if __name__ == "__main__":
    unittest.main()
