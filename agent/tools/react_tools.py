"""Registration and handlers for ReAct tools."""

import os
from pathlib import Path

from agent.execution.policy_engine import ResultClassification
from agent.tools.react_registry import ToolDefinition, register_tool
from agent.tools.run_tests import run_tests
from agent.tools.validation_scope import resolve_inner_loop_validation
from agent_v2.primitives import get_editor


def _search_handler(args: dict, state) -> dict:
    from agent.execution import step_dispatcher as sd

    query = str(args.get("query") or "").strip()
    if not query:
        return {
            "success": False,
            "output": {},
            "error": "SEARCH requires non-empty query. Use Args: {\"query\": \"<search terms>\"}",
            "classification": ResultClassification.RETRYABLE_FAILURE.value,
        }
    raw = sd._search_react(query, state)
    results = raw.get("results") or []
    sd._persist_react_search_to_context(results, state, query)
    return {"success": True, "output": raw, "classification": ResultClassification.SUCCESS.value}


def _read_handler(args: dict, state) -> dict:
    project_root = state.context.get("project_root") or os.environ.get("SERENA_PROJECT_DIR") or os.getcwd()
    path = str(args.get("path") or "").strip()
    if not path:
        return {
            "success": False,
            "output": {},
            "error": "READ requires path. Use Args: {\"path\": \"<file path>\"}",
            "classification": ResultClassification.RETRYABLE_FAILURE.value,
        }
    full_path = Path(path) if Path(path).is_absolute() else Path(project_root) / path
    content = get_editor(state).read(str(full_path.resolve()))
    return {"success": True, "output": content, "classification": ResultClassification.SUCCESS.value}


def _edit_handler(args: dict, state) -> dict:
    from agent.execution import step_dispatcher as sd

    instruction = str(args.get("instruction") or "").strip()
    path = str(args.get("path") or "").strip()
    if not instruction:
        return {
            "success": False,
            "output": {},
            "error": "EDIT requires non-empty instruction. Use Args: {\"instruction\": \"<what to change>\"}",
            "classification": ResultClassification.RETRYABLE_FAILURE.value,
        }
    step = {
        "action": "EDIT",
        "description": instruction,
        "path": path,
        "edit_target_path": path,
    }
    raw = sd._edit_react(step, state)
    return {
        "success": raw.get("success", False),
        "output": raw.get("output", {}),
        "error": raw.get("error"),
        "executed": raw.get("executed", True),
        "classification": ResultClassification.RETRYABLE_FAILURE.value if not raw.get("success") else ResultClassification.SUCCESS.value,
    }


def _run_tests_handler(args: dict, state) -> dict:
    del args
    project_root = state.context.get("project_root") or os.environ.get("SERENA_PROJECT_DIR") or os.getcwd()
    val_scope = resolve_inner_loop_validation(project_root, state.context)
    test_cmd = val_scope.get("test_cmd")
    test_result = run_tests(project_root, timeout=120, test_cmd=test_cmd)
    passed = test_result.get("passed", False)
    stdout = test_result.get("stdout", "") or ""
    stderr = test_result.get("stderr", "") or ""
    return {
        "success": passed,
        "output": {"passed": passed, "stdout": stdout, "stderr": stderr},
        "error": None if passed else (test_result.get("error_type") or "test_failure"),
        "classification": ResultClassification.SUCCESS.value if passed else ResultClassification.RETRYABLE_FAILURE.value,
    }


def register_all_tools() -> None:
    """Register the canonical ReAct tools."""
    register_tool(ToolDefinition("search", "Search the codebase", ["query"], _search_handler))
    register_tool(ToolDefinition("open_file", "Read file contents", ["path"], _read_handler))
    register_tool(ToolDefinition("edit", "Apply edit to file", ["instruction", "path"], _edit_handler))
    register_tool(ToolDefinition("run_tests", "Run tests", [], _run_tests_handler))
    register_tool(ToolDefinition("finish", "Terminate the task", [], None))
