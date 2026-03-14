"""Unit tests for ToolGraph and tool_graph_router."""

import unittest

from agent.execution.tool_graph import ToolGraph
from agent.execution.tool_graph_router import resolve_tool


class TestToolGraph(unittest.TestCase):
    """ToolGraph.get_allowed_tools returns expected lists and handles unknown/disabled."""

    def test_get_allowed_tools_start(self):
        graph = ToolGraph(enabled=True)
        allowed = graph.get_allowed_tools("START")
        self.assertIsInstance(allowed, list)
        self.assertIn("find_symbol", allowed)
        self.assertIn("search_for_pattern", allowed)
        self.assertIn("list_dir", allowed)
        self.assertEqual(len(allowed), 3)

    def test_get_allowed_tools_read_file(self):
        graph = ToolGraph(enabled=True)
        allowed = graph.get_allowed_tools("read_file")
        self.assertIn("find_referencing_symbols", allowed)
        self.assertIn("build_context", allowed)

    def test_get_allowed_tools_find_symbol(self):
        graph = ToolGraph(enabled=True)
        allowed = graph.get_allowed_tools("find_symbol")
        self.assertIn("read_file", allowed)
        self.assertIn("find_referencing_symbols", allowed)

    def test_get_allowed_tools_unknown_node(self):
        graph = ToolGraph(enabled=True)
        allowed = graph.get_allowed_tools("unknown_node")
        self.assertEqual(allowed, [])

    def test_get_allowed_tools_disabled_returns_none(self):
        graph = ToolGraph(enabled=False)
        allowed = graph.get_allowed_tools("START")
        self.assertIsNone(allowed)


class TestToolGraphRouter(unittest.TestCase):
    """resolve_tool: preferred when allowed; fallback to first allowed when not."""

    def test_resolve_preferred_when_allowed(self):
        chosen = resolve_tool("SEARCH", ["find_symbol", "search_for_pattern", "list_dir"])
        self.assertEqual(chosen, "search_for_pattern")

    def test_resolve_fallback_when_preferred_not_allowed(self):
        # Preferred for SEARCH is search_for_pattern; only find_symbol allowed -> fallback
        chosen = resolve_tool("SEARCH", ["find_symbol"])
        self.assertEqual(chosen, "find_symbol")

    def test_resolve_no_restriction_uses_preferred(self):
        chosen = resolve_tool("SEARCH", None)
        self.assertEqual(chosen, "search_for_pattern")
        chosen = resolve_tool("SEARCH", [])
        self.assertEqual(chosen, "search_for_pattern")

    def test_resolve_edit_and_explain(self):
        self.assertEqual(resolve_tool("EDIT", ["edit", "read_file"]), "edit")
        self.assertEqual(resolve_tool("EXPLAIN", ["explain"]), "explain")
