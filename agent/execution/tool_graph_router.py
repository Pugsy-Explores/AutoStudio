"""Router: choose which tool to run from allowed set. Uses graph preferred_tool; for START, action-based."""

import logging

logger = logging.getLogger(__name__)

# For START node only: action → preferred tool (START has no single preferred, depends on action)
ACTION_TO_PREFERRED_TOOL: dict[str, str] = {
    "SEARCH": "retrieve_graph",
    "EDIT": "edit",
    "INFRA": "list_dir",
    "EXPLAIN": "explain",
    "READ_FILE": "read_file",
    "FIND_REFERENCES": "find_referencing_symbols",
    "BUILD_CONTEXT": "build_context",
}


def resolve_tool(
    step_action: str,
    allowed_tools: list[str] | None,
    preferred_tool_from_graph: str | None = None,
    current_node: str = "START",
) -> str:
    """
    Given step action, allowed tools, and graph preferred, return the tool to run.
    For START: use action-based preferred. For other nodes: use graph preferred_tool.
    If preferred not in allowed_tools, fallback to first allowed.
    """
    if current_node == "START":
        preferred = ACTION_TO_PREFERRED_TOOL.get((step_action or "EXPLAIN").upper(), "explain")
    else:
        preferred = preferred_tool_from_graph or (allowed_tools[0] if allowed_tools else "explain")

    if allowed_tools is None or len(allowed_tools) == 0:
        logger.debug("[tool_graph_router] no restriction, chosen=%s", preferred)
        return preferred

    if preferred in allowed_tools:
        logger.info("[tool_graph_router] chosen=%s (preferred)", preferred)
        return preferred

    fallback = allowed_tools[0]
    logger.info("[tool_graph_router] preferred=%s not allowed, fallback to first allowed=%s", preferred, fallback)
    return fallback
