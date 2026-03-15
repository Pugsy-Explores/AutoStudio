"""Dispatch step actions to tool adapters. ToolGraph → Router → PolicyEngine. EXPLAIN uses model_router + chosen model."""

import logging
import os
from pathlib import Path

from agent.execution.explain_gate import ensure_context_before_explain
from config.agent_config import MAX_CONTEXT_CHARS
from agent.execution.policy_engine import ExecutionPolicyEngine, InvalidStepError, _is_valid_search_result, validate_step_input
from agent.execution.tool_graph import ToolGraph
from agent.execution.tool_graph_router import resolve_tool
from agent.memory.state import AgentState
from agent.models.model_client import call_reasoning_model, call_small_model
from agent.models.model_router import get_model_for_task
from agent.models.model_types import ModelType
from agent.observability.trace_logger import log_event, trace_stage
from agent.retrieval import rewrite_query_with_context
from agent.retrieval.context_builder_v2 import assemble_reasoning_context
from agent.retrieval.retrieval_pipeline import run_retrieval_pipeline
from agent.tools import list_files, read_file, run_command, search_code
from config.retrieval_config import (
    ENABLE_HYBRID_RETRIEVAL,
    ENABLE_VECTOR_SEARCH,
    RETRIEVAL_CACHE_SIZE,
)

logger = logging.getLogger(__name__)

_tool_graph = ToolGraph()


def _get_retrieval_cache_size() -> int:
    """Read at runtime so RETRIEVAL_CACHE_SIZE env is respected in tests."""
    return RETRIEVAL_CACHE_SIZE


def _get_retrieval_order(chosen_tool: str | None) -> list[str]:
    """Return retrieval order based on chosen_tool from graph. Puts preferred retriever first."""
    default = ["retrieve_graph", "retrieve_vector", "retrieve_grep"]
    valid = ("retrieve_graph", "retrieve_vector", "retrieve_grep", "list_dir")
    if not chosen_tool or chosen_tool not in valid:
        return default
    order = [chosen_tool]
    for r in default:
        if r not in order:
            order.append(r)
    return order


def _search_fn(query: str, state: AgentState):
    """Raw search result: { results, query }. Cache -> graph -> vector -> Serena fallback."""
    trace_id = state.context.get("trace_id") if state else None
    step_id = state.context.get("current_step_id") if state else None

    def _do_search():
        print(f"[workflow] search query={query!r}")
        project_root = state.context.get("project_root") if state else None
        if project_root is None:
            project_root = os.environ.get("SERENA_PROJECT_DIR") or os.getcwd()

        # Repo map lookup and anchor detection (before retrieval)
        try:
            from agent.retrieval.repo_map_lookup import load_repo_map, lookup_repo_map
            from agent.retrieval.anchor_detector import detect_anchor

            repo_map = load_repo_map(project_root)
            candidates = lookup_repo_map(query, project_root)
            anchor = detect_anchor(query, repo_map)
            state.context["repo_map_anchor"] = anchor
            state.context["repo_map_candidates"] = candidates
        except Exception as e:
            logger.debug("[workflow] repo_map lookup skipped: %s", e)
            state.context["repo_map_anchor"] = None
            state.context["repo_map_candidates"] = []

        cache_size = _get_retrieval_cache_size()
        if cache_size > 0:
            try:
                from agent.retrieval.retrieval_cache import get_cached, set_cached

                cached = get_cached(query, project_root)
                if cached is not None:
                    return cached
            except Exception as e:
                logger.debug("[workflow] cache lookup failed: %s", e)

        if ENABLE_HYBRID_RETRIEVAL:
            try:
                from agent.retrieval.search_pipeline import hybrid_retrieve

                result = hybrid_retrieve(query, state)
                if result and result.get("results"):
                    if cache_size > 0:
                        try:
                            from agent.retrieval.retrieval_cache import set_cached
                            set_cached(query, project_root, result)
                        except Exception:
                            pass
                    return result
            except Exception as e:
                logger.debug("[workflow] hybrid retrieval fallback: %s", e)

        tool_hint = state.context.get("chosen_tool") if state else None
        retrieval_order = _get_retrieval_order(tool_hint)

        result = None
        for retriever in retrieval_order:
            if retriever == "list_dir":
                try:
                    path = query.strip() or "."
                    root = Path(project_root or ".")
                    resolved = (root / path).resolve() if path != "." else root
                    if resolved.is_dir():
                        entries = list_files(str(resolved))
                        result = {
                            "results": [
                                {"file": str(resolved / e), "symbol": "", "line": 0, "snippet": e}
                                for e in entries[:20]
                            ],
                            "query": query,
                        }
                        break
                except Exception as e:
                    logger.debug("[workflow] list_dir fallback: %s", e)
            elif retriever == "retrieve_graph":
                try:
                    from agent.retrieval.graph_retriever import retrieve_symbol_context

                    graph_result = retrieve_symbol_context(query, project_root)
                    if graph_result and graph_result.get("results"):
                        result = graph_result
                        break
                except Exception as e:
                    logger.debug("[workflow] graph retriever fallback: %s", e)
            elif retriever == "retrieve_vector" and ENABLE_VECTOR_SEARCH:
                try:
                    from agent.retrieval.vector_retriever import search_by_embedding

                    vector_result = search_by_embedding(query, project_root, top_k=5)
                    if vector_result and vector_result.get("results"):
                        result = vector_result
                        break
                except Exception as e:
                    logger.debug("[workflow] vector retriever fallback: %s", e)
            elif retriever == "retrieve_grep":
                try:
                    grep_result = search_code(query, tool_hint="search_for_pattern")
                    if grep_result and grep_result.get("results"):
                        result = grep_result
                        break
                except Exception as e:
                    logger.debug("[workflow] grep retriever fallback: %s", e)

        if result is None:
            chosen = state.context.get("chosen_tool") if state else None
            tool_hint = "find_symbol" if chosen == "retrieve_graph" else "search_for_pattern" if chosen == "retrieve_grep" else None
            result = search_code(query, tool_hint=tool_hint)

        if result is None or not isinstance(result, dict):
            result = {"results": [], "query": query or ""}

        # Phase 4: guarantee at least 1 snippet - fallback to file search
        retrieval_fallback_used = None
        if not (result.get("results") or []):
            try:
                root = Path(project_root or ".")
                entries = list_files(str(root))
                if entries:
                    result = {
                        "results": [
                            {"file": str(root / e), "symbol": "", "line": 0, "snippet": e}
                            for e in entries[:10]
                        ],
                        "query": query or "",
                        "retrieval_fallback": "file_search",
                    }
                    retrieval_fallback_used = "file_search"
                    logger.info("[workflow] retrieval empty, fallback to file_search: %d entries", len(entries))
            except Exception as e:
                logger.warning("[workflow] file_search fallback failed: %s", e)

        if retrieval_fallback_used:
            state.context["retrieval_fallback_used"] = retrieval_fallback_used

        if cache_size > 0 and result:
            try:
                from agent.retrieval.retrieval_cache import set_cached

                set_cached(query, project_root, result)
            except Exception:
                pass

        return result

    if trace_id:
        with trace_stage(trace_id, "retrieval", step_id=step_id) as summary:
            result = _do_search()
            summary["query"] = (query or "")[:200]
            results = result.get("results", [])
            summary["results"] = len(results)
            summary["top_files"] = [r.get("file", "")[:80] for r in results[:5]]
            if state.context.get("retrieval_fallback_used"):
                summary["retrieval_fallback"] = state.context["retrieval_fallback_used"]
            return result
    return _do_search()


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
                            try:
                                from repo_graph.repo_map_updater import update_repo_map_for_file
                                update_repo_map_for_file(file_path, project_root)
                            except Exception:
                                pass
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


def _rewrite_for_search(
    description: str, user_request: str, attempt_history: list, state: AgentState | None = None
) -> str | list[str]:
    """Rewrite planner step into a search query; wires rewriter tool choice to state.context['chosen_tool'].
    Returns str or list[str] (query variants); policy engine tries each until success."""
    return rewrite_query_with_context(
        planner_step=description,
        user_request=user_request,
        previous_attempts=attempt_history,
        use_llm=True,
        state=state,
    )


# EXPLAIN system prompt: grounding + scope (BuildRag-style, context engineering best practices)
# - Scope: this agent's codebase (AutoStudio)
# - Grounding: use ONLY provided context; no hallucination from outside knowledge
# - Fallback: when no context, instruct user to run SEARCH first
EXPLAIN_NEEDS_CONTEXT_PREFIX = "I cannot answer without relevant code context"
EXPLAIN_SYSTEM_PROMPT = """You are a code explanation assistant for the AutoStudio agent codebase.

Rules:
- Answer using ONLY the provided context (code snippets, search results). Do not use outside knowledge.
- If no context is provided, or the context does not contain the answer, respond exactly: "I cannot answer without relevant code context. Please run a SEARCH step first to locate the relevant code."
- Keep the answer concise. Focus on architecture, flow, and behavior described in the context.
- When citing code, reference the file path from the context."""


def _format_explain_context(state: AgentState) -> str:
    """Format structured context for EXPLAIN prompt. Prefer ranked_context; fallback to search_memory.
    Uses anchored blocks (FILE/SYMBOL/LINES/SNIPPET) for clear file associations."""
    parts = []
    ranked_context = state.context.get("ranked_context") or []
    if ranked_context:
        assembled = assemble_reasoning_context(ranked_context, max_chars=8000)
        if assembled:
            parts.append("--- BEGIN CONTEXT ---")
            parts.append(assembled.strip())
            parts.append("--- END CONTEXT ---")
            logger.info("[context_anchor] using %d anchored snippets", len(ranked_context))
    else:
        search_memory = state.context.get("search_memory")
        if search_memory and isinstance(search_memory, dict):
            query = search_memory.get("query", "")
            results = search_memory.get("results") or []
            if query or results:
                parts.append("--- BEGIN CONTEXT ---")
                parts.append(f'[source="search" query="{query}"]')
                if results:
                    for i, r in enumerate(results):
                        f = r.get("file") or "(no file)"
                        s = (r.get("snippet") or "").strip()
                        parts.append(f"\n[chunk_id={i} source=\"{f}\"]\n{s[:600]}{'...' if len(s) > 600 else ''}")
                parts.append("\n--- END CONTEXT ---")
        context_snippets = state.context.get("context_snippets") or []
        if context_snippets:
            parts.append("--- BEGIN CONTEXT ---")
            for i, snip in enumerate(context_snippets[:10]):
                if isinstance(snip, dict):
                    f = snip.get("file") or "(no file)"
                    sym = snip.get("symbol") or ""
                    s = (snip.get("snippet") or "").strip()[:600]
                    parts.append(f'\n[chunk_id={i} file="{f}" symbol="{sym}"]\n{s}{"..." if len(snip.get("snippet") or "") > 600 else ""}')
                else:
                    s = (snip if isinstance(snip, str) else str(snip))[:600]
                    parts.append(f'\n[chunk_id={i}]\n{s}{"..." if len(s) >= 600 else ""}')
            parts.append("\n--- END CONTEXT ---")
    if not parts:
        return ""
    return "\n".join(parts) + "\n\n"


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
    try:
        validate_step_input(step)
    except InvalidStepError as e:
        return {"success": False, "output": {}, "error": f"Invalid step: {e}", "classification": "FATAL_FAILURE"}

    action = (step.get("action") or "EXPLAIN").upper()
    description = step.get("description") or ""
    print(f"[workflow] dispatch {action}")

    current_node = state.context.get("tool_node", "START")
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
                run_retrieval_pipeline(out.get("results", []), state, out.get("query"))
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

    # Context gate: avoid LLM call when ranked_context is empty
    has_context, _ = ensure_context_before_explain(step, state)
    if not has_context:
        logger.info("[context_gate] explain requested without context -> injecting SEARCH")
        query = step.get("description", "") or ""
        search_output = _search_fn(query, state)
        results = (search_output or {}).get("results") or []
        if not _is_valid_search_result(results):
            return {
                "success": False,
                "output": "",
                "error": "No context for EXPLAIN. Run SEARCH first.",
            }
        if results:
            run_retrieval_pipeline(
                (search_output or {}).get("results", []),
                state,
                (search_output or {}).get("query"),
            )

    trace_id = state.context.get("trace_id")
    step_id = state.context.get("current_step_id")
    try:
        model_type = get_model_for_task("EXPLAIN")
        model_name = model_type.value if model_type else "REASONING"
        print("  [EXPLAIN] model from config:", model_name)
        context_block = _format_explain_context(state)
        # Context guardrail: hard cap before LLM call
        if context_block and len(context_block) > MAX_CONTEXT_CHARS:
            original_len = len(context_block)
            context_block = context_block[:MAX_CONTEXT_CHARS] + "\n\n[context truncated by guardrail]"
            if trace_id:
                log_event(
                    trace_id,
                    "context_guardrail_triggered",
                    {"original_chars": original_len, "capped_chars": MAX_CONTEXT_CHARS},
                )
            logger.info("[context_guardrail] truncated context from %d to %d chars", original_len, MAX_CONTEXT_CHARS)
        if context_block:
            user_prompt = f"Question:\n{description}\n\nContext:\n{context_block}"
        else:
            user_prompt = f"Question:\n{description}\n\nContext:\n(none provided - run a SEARCH step first to locate the relevant code)"
        if trace_id:
            with trace_stage(trace_id, "reasoning", step_id=step_id) as summary:
                summary["question"] = (description or "")[:300]
                summary["context_chars"] = len(context_block) if context_block else 0
                try:
                    if model_type == ModelType.SMALL:
                        out = call_small_model(
                            user_prompt,
                            task_name="EXPLAIN",
                            system_prompt=EXPLAIN_SYSTEM_PROMPT,
                        )
                    else:
                        out = call_reasoning_model(
                            user_prompt,
                            system_prompt=EXPLAIN_SYSTEM_PROMPT,
                            task_name="EXPLAIN",
                        )
                    out_str = (out or "").strip() or "[EXPLAIN: no model output]"
                    summary["first_200_chars"] = out_str[:200]
                    print("  [model] output:", out_str[:120] + ("..." if len(out_str) > 120 else ""))
                    return {"success": True, "output": out_str}
                except Exception as e:
                    summary["error"] = str(e)[:200]
                    raise
        if model_type == ModelType.SMALL:
            out = call_small_model(
                user_prompt,
                task_name="EXPLAIN",
                system_prompt=EXPLAIN_SYSTEM_PROMPT,
            )
        else:
            out = call_reasoning_model(
                user_prompt,
                system_prompt=EXPLAIN_SYSTEM_PROMPT,
                task_name="EXPLAIN",
            )
        out_str = (out or "").strip() or "[EXPLAIN: no model output]"
        print("  [model] output:", out_str[:120] + ("..." if len(out_str) > 120 else ""))
        return {"success": True, "output": out_str}
    except Exception as e:
        return {"success": False, "output": "", "error": str(e)}
