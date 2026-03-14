"""Tool graph: restricts which tools are allowed from each node. Used before Router + PolicyEngine."""

import logging
import os

logger = logging.getLogger(__name__)

ENABLE_TOOL_GRAPH = os.environ.get("ENABLE_TOOL_GRAPH", "1").lower() in ("1", "true", "yes")


def _normalize_node_value(val) -> tuple[list[str], str | None]:
    """Extract (allowed_tools, preferred_tool) from node value. Supports legacy list format."""
    if isinstance(val, list):
        tools = list(val) if val else []
        preferred = tools[0] if tools else None
        return (tools, preferred)
    if isinstance(val, dict):
        tools = val.get("allowed_tools") or val.get("allowed") or []
        preferred = val.get("preferred_tool") or val.get("preferred")
        return (list(tools), preferred)
    return ([], None)


class ToolGraph:
    """
    Directed graph of tool names. From each node: allowed_tools and preferred_tool.
    Router chooses preferred if in allowed, else first allowed.
    """

    GRAPH = {
        "START": {
            "allowed_tools": ["find_symbol", "search_for_pattern", "list_dir"],
            "preferred_tool": "search_for_pattern",
        },
        "find_symbol": {
            "allowed_tools": ["read_file", "find_referencing_symbols"],
            "preferred_tool": "read_file",
        },
        "search_for_pattern": {
            "allowed_tools": ["read_file"],
            "preferred_tool": "read_file",
        },
        "read_file": {
            "allowed_tools": ["find_referencing_symbols", "build_context"],
            "preferred_tool": "find_referencing_symbols",
        },
        "find_referencing_symbols": {
            "allowed_tools": ["read_file", "build_context"],
            "preferred_tool": "read_file",
        },
        "build_context": {
            "allowed_tools": ["explain", "edit"],
            "preferred_tool": "explain",
        },
        "list_dir": {
            "allowed_tools": ["read_file", "search_for_pattern"],
            "preferred_tool": "read_file",
        },
        "explain": {"allowed_tools": [], "preferred_tool": None},
        "edit": {"allowed_tools": [], "preferred_tool": None},
    }

    def __init__(self, enabled: bool | None = None):
        self._enabled = enabled if enabled is not None else ENABLE_TOOL_GRAPH

    def get_allowed_tools(self, current_node: str) -> list[str] | None:
        """
        Return list of allowed tool names from current_node.
        If tool graph is disabled, return None (dispatcher treats as "all allowed").
        If node is unknown, return [] (permissive: no restriction).
        """
        if not self._enabled:
            logger.debug("[tool_graph] disabled, no restriction")
            return None
        val = self.GRAPH.get(current_node)
        if val is None:
            logger.info("[tool_graph] node=%s unknown, no restriction", current_node)
            return []
        allowed, _ = _normalize_node_value(val)
        logger.info("[tool_graph] node=%s → allowed=%s", current_node, allowed)
        return allowed

    def get_preferred_tool(self, current_node: str) -> str | None:
        """Return preferred tool for current_node, or None."""
        if not self._enabled:
            return None
        val = self.GRAPH.get(current_node)
        if val is None:
            return None
        _, preferred = _normalize_node_value(val)
        return preferred
