"""Dispatch step actions to tool adapters. EXPLAIN uses model_router + chosen model."""

from agent.execution.policy_engine import ExecutionPolicyEngine
from agent.memory.state import AgentState
from agent.models.model_client import call_reasoning_model, call_small_model
from agent.models.model_router import get_model_for_task
from agent.models.model_types import ModelType
from agent.retrieval import rewrite_query_with_context
from agent.tools import list_files, read_file, run_command, search_code


def _search_fn(query: str):
    """Raw search result: { results, query }."""
    print(f"[workflow] search query={query!r}")
    return search_code(query)


def _edit_fn(step: dict, state: AgentState) -> dict:
    """Dispatch-style: { success, output, error }."""
    try:
        path = state.context.get("edit_path")
        if path:
            print("  [read_file] path:", path)
            content = read_file(path)
            out = {"path": path, "content_preview": content[:500] + "..." if len(content) > 500 else content}
        else:
            print("  [list_files] cwd (no edit_path)")
            listing = list_files(".")
            out = {"message": "No path in context; listed cwd", "files": listing}
        return {"success": True, "output": out}
    except Exception as e:
        return {"success": False, "output": {}, "error": str(e)}


def _infra_fn(step: dict, state: AgentState) -> dict:
    """Dispatch-style: { success, output, error }; output has returncode."""
    try:
        print("  [run_command] true")
        cmd_result = run_command("true")
        print("  [list_files] .")
        out = {"list_files": list_files("."), "run_command": cmd_result}
        out["returncode"] = cmd_result.get("returncode", -1)
        return {"success": True, "output": out}
    except Exception as e:
        return {"success": False, "output": {"returncode": -1}, "error": str(e)}


def _rewrite_for_search(description: str, user_request: str, attempt_history: list) -> str:
    """Rewrite planner step into a search query using execution context (attempt history)."""
    return rewrite_query_with_context(
        planner_step=description,
        user_request=user_request,
        previous_attempts=attempt_history,
        use_llm=True,
    )


_policy_engine = ExecutionPolicyEngine(
    search_fn=_search_fn,
    edit_fn=_edit_fn,
    infra_fn=_infra_fn,
    rewrite_query_fn=_rewrite_for_search,
    max_total_attempts=10,
)


def dispatch(step: dict, state: AgentState) -> dict:
    """
    Map step action to tool call. Returns dict with success, output, error (optional).
    INFRA output must include returncode for validator.
    SEARCH/EDIT/INFRA go through ExecutionPolicyEngine (retry + mutation).
    """
    action = (step.get("action") or "EXPLAIN").upper()
    description = step.get("description") or ""
    print(f"[workflow] dispatch {action}")

    if action == "SEARCH":
        return _policy_engine.execute_with_policy(step, state)

    if action == "EDIT":
        return _policy_engine.execute_with_policy(step, state)

    if action == "INFRA":
        return _policy_engine.execute_with_policy(step, state)

    # EXPLAIN or unknown: use config task_models["EXPLAIN"] then call chosen model
    try:
        model_type = get_model_for_task("EXPLAIN")
        model_name = "REASONING" if model_type == ModelType.REASONING else "SMALL"
        print("  [EXPLAIN] model from config:", model_name)
        prompt = f"Briefly explain or address this task: {description}"
        if model_type == ModelType.REASONING:
            out = call_reasoning_model(prompt, task_name="EXPLAIN")
        else:
            out = call_small_model(prompt, task_name="EXPLAIN")
        out_str = (out or "").strip() or "[EXPLAIN: no model output]"
        print("  [model] output:", out_str[:120] + ("..." if len(out_str) > 120 else ""))
        return {"success": True, "output": out_str}
    except Exception as e:
        return {"success": False, "output": "", "error": str(e)}
