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


def _search_multi_handler(args: dict, state) -> list[dict]:
    """Internal multi-query search: ``retrieve(queries)`` (batch vector + per-query RRF + rerank).

    Returns one tool payload dict per query, in order — consumed by Dispatcher.execute (Option A).
    """
    from agent.retrieval.retrieval_pipeline_v2 import (  # noqa: PLC0415
        retrieve,
        search_payload_from_retrieval_output,
    )

    raw_q = args.get("queries")
    if not isinstance(raw_q, list) or not raw_q:
        err = {
            "success": False,
            "output": {},
            "error": "search_multi requires non-empty queries list",
            "classification": ResultClassification.RETRYABLE_FAILURE.value,
        }
        return [err]
    queries = [str(q).strip() for q in raw_q if q and str(q).strip()]
    if not queries:
        err = {
            "success": False,
            "output": {},
            "error": "search_multi: all queries empty",
            "classification": ResultClassification.RETRYABLE_FAILURE.value,
        }
        return [err]
    pr = state.context.get("project_root") if state else None
    outs = retrieve(queries, state=state, project_root=pr)
    payloads = [search_payload_from_retrieval_output(o) for o in outs]
    return [
        {
            "success": True,
            "output": p,
            "classification": ResultClassification.SUCCESS.value,
        }
        for p in payloads
    ]


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


def _read_snippet_handler(args: dict, state) -> dict:
    """
    Internal bounded read tool.

    Not intended for LLM selection; used by system-driven exploration inspection to guarantee
    bound-before-I/O (no full-file reads).
    """
    from agent_v2.exploration.read_router import ReadRequest, read as bounded_read  # noqa: PLC0415

    project_root = state.context.get("project_root") or os.environ.get("SERENA_PROJECT_DIR") or os.getcwd()
    path = str(args.get("path") or "").strip()
    if not path:
        return {
            "success": False,
            "output": {},
            "error": "READ_SNIPPET requires path. Args: {\"path\": \"<file path>\", \"symbol\"?: str, \"line\"?: int, \"window\"?: int}",
            "classification": ResultClassification.RETRYABLE_FAILURE.value,
        }

    # Resolve path relative to project root for deterministic behavior.
    full_path = Path(path) if Path(path).is_absolute() else Path(project_root) / path
    symbol = args.get("symbol")
    line = args.get("line")
    window = args.get("window")

    req = ReadRequest(
        path=str(full_path),
        symbol=str(symbol).strip() if isinstance(symbol, str) and symbol.strip() else None,
        line=int(line) if isinstance(line, int) or (isinstance(line, str) and str(line).strip().isdigit()) else None,
        window=int(window) if isinstance(window, int) or (isinstance(window, str) and str(window).strip().isdigit()) else 80,
    )
    payload = bounded_read(req, state=state)
    return {"success": True, "output": payload, "classification": ResultClassification.SUCCESS.value}


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
    register_tool(
        ToolDefinition(
            "search_multi",
            "Batch search (internal — exploration/dispatcher only)",
            ["queries"],
            _search_multi_handler,
        )
    )
    register_tool(ToolDefinition("open_file", "Read file contents", ["path"], _read_handler))
    # Internal-only bounded read primitive (used by system exploration; not part of user-facing ReAct contract).
    register_tool(ToolDefinition("read_snippet", "Read bounded snippet (internal)", ["path"], _read_snippet_handler))
    register_tool(ToolDefinition("edit", "Apply edit to file", ["instruction", "path"], _edit_handler))
    register_tool(ToolDefinition("run_tests", "Run tests", [], _run_tests_handler))
    register_tool(ToolDefinition("finish", "Terminate the task", [], None))
