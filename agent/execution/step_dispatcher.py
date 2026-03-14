"""Dispatch step actions to tool adapters. ToolGraph → Router → PolicyEngine. EXPLAIN uses model_router + chosen model."""

import logging
import os

from agent.execution.policy_engine import ExecutionPolicyEngine
from agent.execution.tool_graph import ToolGraph
from agent.execution.tool_graph_router import resolve_tool
from agent.memory.state import AgentState
from agent.models.model_client import call_reasoning_model, call_small_model
from agent.models.model_router import get_model_for_task
from agent.models.model_types import ModelType
from agent.retrieval import rewrite_query_with_context
from agent.retrieval.context_builder import build_context_from_symbols
from agent.retrieval.context_pruner import prune_context
from agent.retrieval.context_ranker import rank_context
from agent.retrieval.retrieval_expander import expand_search_results
from agent.tools import (
    find_referencing_symbols,
    list_files,
    read_file,
    read_symbol_body,
    run_command,
    search_code,
)

logger = logging.getLogger(__name__)

ENABLE_CONTEXT_RANKING = os.environ.get("ENABLE_CONTEXT_RANKING", "1").lower() in ("1", "true", "yes")
_tool_graph = ToolGraph()


def _build_candidates_from_context(built: dict) -> list[dict]:
    """Build ranker candidates from context_builder output: {file, symbol, snippet, type}."""
    candidates: list[dict] = []
    for s in built.get("symbols") or []:
        if isinstance(s, dict):
            candidates.append({
                "file": s.get("file") or "",
                "symbol": s.get("symbol") or "",
                "snippet": s.get("snippet") or "",
                "type": "symbol",
            })
    for r in built.get("references") or []:
        if isinstance(r, dict):
            snippet = r.get("snippet") or f"{r.get('symbol', '')} at line {r.get('line', '?')}"
            candidates.append({
                "file": r.get("file") or "",
                "symbol": r.get("symbol") or "",
                "snippet": snippet,
                "type": "reference",
            })
    files = built.get("files") or []
    snippets = built.get("snippets") or []
    for i, path in enumerate(files):
        snip = snippets[i] if i < len(snippets) else ""
        candidates.append({
            "file": path,
            "symbol": "",
            "snippet": snip,
            "type": "file",
        })
    return candidates


ENABLE_VECTOR_SEARCH = os.environ.get("ENABLE_VECTOR_SEARCH", "1").lower() in ("1", "true", "yes")
RETRIEVAL_CACHE_SIZE = int(os.environ.get("RETRIEVAL_CACHE_SIZE", "100"))


def _search_fn(query: str, state: AgentState):
    """Raw search result: { results, query }. Cache -> graph -> vector -> Serena fallback."""
    print(f"[workflow] search query={query!r}")
    project_root = state.context.get("project_root") if state else None
    if project_root is None:
        project_root = os.environ.get("SERENA_PROJECT_DIR") or os.getcwd()

    if RETRIEVAL_CACHE_SIZE > 0:
        try:
            from agent.retrieval.retrieval_cache import get_cached, set_cached

            cached = get_cached(query, project_root)
            if cached is not None:
                return cached
        except Exception as e:
            logger.debug("[workflow] cache lookup failed: %s", e)

    result = None
    try:
        from agent.retrieval.graph_retriever import retrieve_symbol_context

        graph_result = retrieve_symbol_context(query, project_root)
        if graph_result and graph_result.get("results"):
            result = graph_result
    except Exception as e:
        logger.debug("[workflow] graph retriever fallback: %s", e)

    if result is None and ENABLE_VECTOR_SEARCH:
        try:
            from agent.retrieval.vector_retriever import search_by_embedding

            vector_result = search_by_embedding(query, project_root, top_k=5)
            if vector_result and vector_result.get("results"):
                result = vector_result
        except Exception as e:
            logger.debug("[workflow] vector retriever fallback: %s", e)

    if result is None:
        tool_hint = state.context.get("chosen_tool") if state else None
        if tool_hint not in ("find_symbol", "search_for_pattern"):
            tool_hint = None
        result = search_code(query, tool_hint=tool_hint)

    if RETRIEVAL_CACHE_SIZE > 0 and result:
        try:
            from agent.retrieval.retrieval_cache import set_cached

            set_cached(query, project_root, result)
        except Exception:
            pass

    return result


def _edit_fn(step: dict, state: AgentState) -> dict:
    """Dispatch-style: { success, output, error }. When ENABLE_DIFF_PLANNER: plan -> patch_executor -> update_index."""
    try:
        try:
            from editing.diff_planner import ENABLE_DIFF_PLANNER, plan_diff
            from editing.patch_executor import execute_patch
            from editing.patch_generator import to_structured_patches
            from repo_index.indexer import update_index_for_file

            if ENABLE_DIFF_PLANNER:
                instruction = step.get("description") or ""
                plan = plan_diff(instruction, state.context)
                changes = plan.get("changes", [])
                logger.info("[edit_planner] patches=%d", len(changes))

                if changes:
                    patch_plan = to_structured_patches(plan, instruction, state.context)
                    project_root = state.context.get("project_root") or os.environ.get("SERENA_PROJECT_DIR") or os.getcwd()
                    result = execute_patch(patch_plan, project_root)

                    if result.get("success"):
                        for file_path in result.get("files_modified", []):
                            update_index_for_file(file_path, project_root)
                        return {
                            "success": True,
                            "output": {
                                "files_modified": result.get("files_modified", []),
                                "patches_applied": result.get("patches_applied", 0),
                                "planned_changes": changes,
                            },
                        }
                    return {
                        "success": False,
                        "output": {
                            "error": result.get("error", "patch_failed"),
                            "reason": result.get("reason", ""),
                            "file": result.get("file", ""),
                        },
                        "error": result.get("reason", "") or result.get("error", "patch_failed"),
                    }

                out = {"planned_changes": changes, **plan}
                return {"success": True, "output": out}
        except ImportError:
            pass

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


def _format_explain_context(state: AgentState) -> str:
    """Format structured context for EXPLAIN prompt. Prefer ranked_context; fallback to search_memory."""
    parts = []
    ranked_context = state.context.get("ranked_context") or []
    if ranked_context:
        parts.append("Context snippets:")
        for c in ranked_context:
            file_path = c.get("file") or "(no file)"
            symbol = c.get("symbol") or ""
            snippet = (c.get("snippet") or "").strip()
            parts.append(f"* file: {file_path}")
            if symbol:
                parts.append(f"* symbol: {symbol}")
            parts.append(f"* snippet: {snippet[:500]}{'...' if len(snippet) > 500 else ''}")
        logger.info("[explain_context] using %d ranked snippets", len(ranked_context))
    else:
        search_memory = state.context.get("search_memory")
        if search_memory and isinstance(search_memory, dict):
            query = search_memory.get("query", "")
            results = search_memory.get("results") or []
            if query or results:
                parts.append("Context from previous search:")
                parts.append(f"  Query: {query}")
                if results:
                    parts.append("  Results:")
                    for r in results:
                        f = r.get("file") or "(no file)"
                        s = (r.get("snippet") or "").strip()
                        parts.append(f"    - {f}: {s[:400]}{'...' if len(s) > 400 else ''}")
        context_snippets = state.context.get("context_snippets") or []
        if context_snippets:
            parts.append("Code snippets:")
            for i, snip in enumerate(context_snippets[:10]):
                s = (snip if isinstance(snip, str) else str(snip))[:500]
                parts.append(f"  [{i+1}] {s}{'...' if len(s) >= 500 else ''}")
    if not parts:
        return ""
    return "\n".join(parts) + "\n\n"


def _run_retrieval_expansion(state: AgentState, search_output: dict) -> None:
    """After SEARCH success: expand results -> read_symbol_body/read_file -> find_references -> build_context; update state."""
    results = search_output.get("results") or []
    if not results:
        return
    expanded = expand_search_results(results)
    if not expanded:
        return
    symbol_results = []
    reference_results = []
    file_snippets = []
    for item in expanded:
        path = item.get("file") or ""
        symbol = item.get("symbol") or ""
        action_type = item.get("action") or "read_file"
        line = item.get("line")
        try:
            if action_type == "read_symbol_body" and symbol:
                body = read_symbol_body(symbol, path, line=line)
                file_snippets.append({"file": path, "snippet": body, "symbol": symbol})
                symbol_results.append({"file": path, "symbol": symbol, "snippet": body[:500]})
            else:
                content = read_file(path)
                snip = (content or "")[:2000]
                file_snippets.append({"file": path, "snippet": snip})
            refs = find_referencing_symbols(symbol or path, path)
            reference_results.extend(refs)
        except Exception as e:
            logger.warning("[workflow] retrieval expand %s: %s", path, e)
    built = build_context_from_symbols(symbol_results, reference_results, file_snippets)
    state.context["retrieved_symbols"] = built.get("symbols", [])
    state.context["retrieved_references"] = built.get("references", [])
    state.context["retrieved_files"] = built.get("files", [])
    state.context["context_snippets"] = built.get("snippets", [])

    candidates = _build_candidates_from_context(built)
    state.context["context_candidates"] = candidates
    if ENABLE_CONTEXT_RANKING and candidates:
        query = search_output.get("query") or state.instruction or ""
        ranked = rank_context(query, candidates)
        final_context = prune_context(ranked, max_snippets=6, max_chars=8000)
        state.context["ranked_context"] = final_context
        state.context["ranking_scores"] = []  # optional: store per-candidate scores
    else:
        state.context["ranked_context"] = []

    search_memory = state.context.get("search_memory") or {}
    if isinstance(search_memory, dict):
        search_memory = dict(search_memory)
        existing = search_memory.get("results") or []
        for s in built.get("snippets", [])[:5]:
            existing.append({"file": "", "snippet": s[:500]})
        search_memory["results"] = existing
        state.context["search_memory"] = search_memory


_policy_engine = ExecutionPolicyEngine(
    search_fn=_search_fn,
    edit_fn=_edit_fn,
    infra_fn=_infra_fn,
    rewrite_query_fn=_rewrite_for_search,
    max_total_attempts=10,
)


def dispatch(step: dict, state: AgentState) -> dict:
    """
    Map step action to tool call. ToolGraph restricts tools; Router chooses (with fallback); PolicyEngine runs.
    Returns dict with success, output, error (optional).
    """
    action = (step.get("action") or "EXPLAIN").upper()
    description = step.get("description") or ""
    print(f"[workflow] dispatch {action}")

    current_node = state.context.get("tool_node", "START")
    if action == "SEARCH":
        current_node = "START"
    allowed_tools = _tool_graph.get_allowed_tools(current_node)
    preferred_from_graph = _tool_graph.get_preferred_tool(current_node)
    chosen_tool = resolve_tool(action, allowed_tools, preferred_from_graph, current_node)
    state.context["chosen_tool"] = chosen_tool

    if action == "SEARCH":
        raw = _policy_engine.execute_with_policy(step, state)
        state.context["tool_node"] = chosen_tool
        if raw.get("success") and raw.get("output"):
            out = raw["output"]
            if isinstance(out, dict) and (out.get("results") or []):
                _run_retrieval_expansion(state, out)
        return raw

    if action == "EDIT":
        raw = _policy_engine.execute_with_policy(step, state)
        state.context["tool_node"] = chosen_tool
        return raw

    if action == "INFRA":
        raw = _policy_engine.execute_with_policy(step, state)
        state.context["tool_node"] = chosen_tool
        return raw

    # EXPLAIN or unknown: use config task_models["EXPLAIN"] then call chosen model
    state.context["tool_node"] = chosen_tool
    try:
        model_type = get_model_for_task("EXPLAIN")
        model_name = model_type.value if model_type else "REASONING"
        print("  [EXPLAIN] model from config:", model_name)
        context_block = _format_explain_context(state)
        task_line = f"Briefly explain or address this task: {description}"
        prompt = (context_block + task_line) if context_block else task_line
        if model_type == ModelType.SMALL:
            out = call_small_model(prompt, task_name="EXPLAIN")
        else:
            out = call_reasoning_model(prompt, task_name="EXPLAIN")
        out_str = (out or "").strip() or "[EXPLAIN: no model output]"
        print("  [model] output:", out_str[:120] + ("..." if len(out_str) > 120 else ""))
        return {"success": True, "output": out_str}
    except Exception as e:
        return {"success": False, "output": "", "error": str(e)}
