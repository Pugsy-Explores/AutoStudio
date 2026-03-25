"""Central tool registry for ReAct tools. Single source of truth for schema and dispatch."""

from dataclasses import dataclass
from typing import Any, Callable

# Type alias: handler(args, state) -> {success, output, error?, classification?, executed?}
ReactHandler = Callable[[dict, Any], dict]


@dataclass
class ToolDefinition:
    """ReAct tool definition. Handler is None for finish (never dispatched)."""

    name: str  # ReAct name: search, open_file, edit, run_tests, finish
    description: str  # For documentation
    required_args: list[str]  # For validation
    handler: ReactHandler | None  # (args, state) -> dict; None for finish


_by_name: dict[str, ToolDefinition] = {}
_initialized: bool = False


def clear_tool_registry() -> None:
    """Reset registry; mainly for tests."""
    global _initialized
    _by_name.clear()
    _initialized = False


def register_tool(tool: ToolDefinition) -> None:
    """Register a ReAct tool."""
    _by_name[tool.name] = tool


def get_tool_by_name(name: str) -> ToolDefinition | None:
    """Get tool by ReAct name (search, open_file, edit, run_tests, finish)."""
    return _by_name.get(name)


def get_all_tools() -> list[ToolDefinition]:
    """Return all registered tools."""
    return list(_by_name.values())


def initialize_tool_registry() -> None:
    """Initialize registry exactly once by registering all ReAct tools."""
    global _initialized
    if _initialized:
        return
    from agent.tools.react_tools import register_all_tools

    register_all_tools()
    _initialized = True
