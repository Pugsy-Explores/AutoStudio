"""Unit tests for ToolGraph and tool_graph_router.

Step 6 — Dispatcher and Tool Graph:
- step type → correct tool mapping
- SEARCH → retrieval_pipeline (retrieve_graph preferred)
- EXPLAIN → reasoning model (explain)
- EDIT → editing pipeline (edit)
"""

import unittest

from agent.execution.tool_graph import ToolGraph
from agent.execution.tool_graph_router import resolve_tool, ACTION_TO_PREFERRED_TOOL


class TestToolGraph(unittest.TestCase):
    """ToolGraph.get_allowed_tools returns expected lists and handles unknown/disabled."""

    def test_get_allowed_tools_start(self):
        graph = ToolGraph(enabled=True)
        allowed = graph.get_allowed_tools("START")
        self.assertIsInstance(allowed, list)
        self.assertIn("retrieve_graph", allowed)
        self.assertIn("retrieve_vector", allowed)
        self.assertIn("retrieve_grep", allowed)
        self.assertIn("list_dir", allowed)
        self.assertEqual(len(allowed), 4)

    def test_get_allowed_tools_read_file(self):
        graph = ToolGraph(enabled=True)
        allowed = graph.get_allowed_tools("read_file")
        self.assertIn("find_referencing_symbols", allowed)
        self.assertIn("build_context", allowed)

    def test_get_allowed_tools_retrieve_graph(self):
        graph = ToolGraph(enabled=True)
        allowed = graph.get_allowed_tools("retrieve_graph")
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
        chosen = resolve_tool("SEARCH", ["retrieve_graph", "retrieve_vector", "retrieve_grep", "list_dir"])
        self.assertEqual(chosen, "retrieve_graph")

    def test_resolve_fallback_when_preferred_not_allowed(self):
        # Preferred for SEARCH is retrieve_graph; only retrieve_vector allowed -> fallback
        chosen = resolve_tool("SEARCH", ["retrieve_vector"])
        self.assertEqual(chosen, "retrieve_vector")

    def test_resolve_no_restriction_uses_preferred(self):
        chosen = resolve_tool("SEARCH", None)
        self.assertEqual(chosen, "retrieve_graph")
        chosen = resolve_tool("SEARCH", [])
        self.assertEqual(chosen, "retrieve_graph")

    def test_resolve_edit_and_explain(self):
        self.assertEqual(resolve_tool("EDIT", ["edit", "read_file"]), "edit")
        self.assertEqual(resolve_tool("EXPLAIN", ["explain"]), "explain")

    def test_step_type_maps_to_correct_tool(self):
        """Step 6: SEARCH→retrieval, EXPLAIN→reasoning, EDIT→editing pipeline."""
        allowed_search = ["retrieve_graph", "retrieve_vector", "retrieve_grep", "list_dir"]
        self.assertEqual(resolve_tool("SEARCH", allowed_search), "retrieve_graph")
        self.assertEqual(resolve_tool("EXPLAIN", ["explain"]), "explain")
        self.assertEqual(resolve_tool("EDIT", ["edit"]), "edit")
        self.assertEqual(resolve_tool("INFRA", ["list_dir"]), "list_dir")

    def test_action_to_preferred_tool_mapping(self):
        """ACTION_TO_PREFERRED_TOOL defines step→tool mapping."""
        self.assertEqual(ACTION_TO_PREFERRED_TOOL["SEARCH"], "retrieve_graph")
        self.assertEqual(ACTION_TO_PREFERRED_TOOL["EXPLAIN"], "explain")
        self.assertEqual(ACTION_TO_PREFERRED_TOOL["EDIT"], "edit")
