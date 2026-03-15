"""Tests for agent/retrieval/context_builder_v2."""

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
