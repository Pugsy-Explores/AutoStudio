"""Tests for agent/retrieval/context_builder_v2.

Step 7 — Context Builder:
- context size, context ordering, relevance
- LLM input: retrieved code, relevant snippets, minimal noise
"""

import unittest

from agent.retrieval.context_builder_v2 import assemble_reasoning_context


class TestAssembleReasoningContext(unittest.TestCase):
    def test_empty_snippets_returns_empty(self):
        self.assertEqual(assemble_reasoning_context([]), "")

    def test_single_snippet_format(self):
        snippets = [
            {"file": "executor.py", "symbol": "StepExecutor", "snippet": "class StepExecutor:\n    pass", "line_range": [40, 80]},
        ]
        out = assemble_reasoning_context(snippets)
        self.assertIn("FILE: executor.py", out)
        self.assertIn("SYMBOL: StepExecutor", out)
        self.assertIn("LINES: 40-80", out)
        self.assertIn("SNIPPET:", out)
        self.assertIn("class StepExecutor:", out)

    def test_deduplicate_by_file_symbol(self):
        snippets = [
            {"file": "a.py", "symbol": "foo", "snippet": "def foo(): pass"},
            {"file": "a.py", "symbol": "foo", "snippet": "def foo(): pass"},
        ]
        out = assemble_reasoning_context(snippets)
        self.assertEqual(out.count("FILE: a.py"), 1)

    def test_max_chars_respected(self):
        snippets = [
            {"file": "f.py", "symbol": "x", "snippet": "x" * 5000},
            {"file": "g.py", "symbol": "y", "snippet": "y" * 5000},
        ]
        out = assemble_reasoning_context(snippets, max_chars=6000)
        self.assertLessEqual(len(out), 6500)

    def test_context_ordering_preserved(self):
        """Step 7: snippets appear in input order (relevance ordering from ranker)."""
        snippets = [
            {"file": "first.py", "symbol": "A", "snippet": "def A(): pass"},
            {"file": "second.py", "symbol": "B", "snippet": "def B(): pass"},
        ]
        out = assemble_reasoning_context(snippets)
        idx_a = out.find("FILE: first.py")
        idx_b = out.find("FILE: second.py")
        self.assertGreater(idx_b, idx_a, "First snippet should appear before second")

    def test_context_contains_retrieved_code_and_snippets(self):
        """Step 7: LLM input must contain retrieved code, relevant snippets, minimal noise."""
        snippets = [
            {"file": "agent/executor.py", "symbol": "StepExecutor", "snippet": "class StepExecutor:\n    def execute_step(self): ...", "line_range": [1, 20]},
        ]
        out = assemble_reasoning_context(snippets)
        self.assertIn("FILE: agent/executor.py", out)
        self.assertIn("SYMBOL: StepExecutor", out)
        self.assertIn("LINES: 1-20", out)
        self.assertIn("SNIPPET:", out)
        self.assertIn("class StepExecutor:", out)
