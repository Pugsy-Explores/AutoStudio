"""Dispatch step actions to tool adapters. ToolGraph → Router → PolicyEngine. EXPLAIN uses model_router + chosen model."""

import logging
import os
from pathlib import Path

from agent.execution.explain_gate import ensure_context_before_explain
from agent.prompt_system import get_registry
from config.agent_config import MAX_CONTEXT_CHARS
from agent.execution.policy_engine import ExecutionPolicyEngine, InvalidStepError, ResultClassification, _is_valid_search_result, classify_result, validate_step_input
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
from agent.tools import build_context, list_files, read_file, run_command, search_candidates, search_code
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
    """Dispatch-style: { success, output, error }. Pipeline: plan_diff -> resolve_conflicts -> run_edit_test_fix_loop (single repair path)."""
    try:
        from config.agent_runtime import MAX_EDIT_ATTEMPTS
        from config.editing_config import MAX_FILES_EDITED, MAX_PATCH_SIZE
        from editing.conflict_resolver import resolve_conflicts
        from editing.diff_planner import plan_diff
        from agent.runtime.execution_loop import run_edit_test_fix_loop
        from repo_graph.change_detector import RISK_HIGH, detect_change_impact
        from repo_index.indexer import update_index_for_file
    except ImportError:
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

    instruction = step.get("description") or ""
    context = state.context
    project_root = context.get("project_root") or os.environ.get("SERENA_PROJECT_DIR") or os.getcwd()
    context["instruction"] = instruction

    diff_plan = plan_diff(instruction, context)
    changes = diff_plan.get("changes", [])
    if not changes:
        return {"success": True, "output": {"planned_changes": changes}}

    # Safety limits
    if len(changes) > MAX_FILES_EDITED:
        return {
            "success": False,
            "output": {"error": "max_files_exceeded"},
            "error": f"max files exceeded ({len(changes)} > {MAX_FILES_EDITED})",
        }
    for c in changes:
        patch_text = c.get("patch", "")
        if isinstance(patch_text, str) and patch_text.count("\n") >= MAX_PATCH_SIZE:
            return {
                "success": False,
                "output": {"error": "max_patch_size_exceeded"},
                "error": "max patch size exceeded",
            }

    # Change detection (before apply) for risk assessment
    edited_symbols = [(c.get("file", ""), c.get("symbol", "")) for c in changes]
    impact = detect_change_impact(edited_symbols, project_root)
    trace_id = context.get("trace_id")
    if impact.get("risk_level") == RISK_HIGH and trace_id:
        from agent.observability.trace_logger import log_event
        log_event(trace_id, "high_risk_edit", {"impact": impact})

    # Conflict resolution (informational; execution loop re-plans each attempt)
    resolve_result = resolve_conflicts(diff_plan)
    if resolve_result.get("valid"):
        groups = [changes]
    else:
        groups = resolve_result.get("sequential_groups", [changes])

    all_modified: list = []
    all_patches = 0
    for group in groups:
        if not group:
            continue
        loop_result = run_edit_test_fix_loop(
            instruction, context, project_root, max_attempts=MAX_EDIT_ATTEMPTS
        )
        if not loop_result.get("success"):
            return {
                "success": False,
                "output": {
                    "error": loop_result.get("error"),
                    "reason": loop_result.get("reason"),
                },
                "error": loop_result.get("reason", loop_result.get("error")),
            }
        all_modified.extend(loop_result.get("files_modified", []))
        all_patches += loop_result.get("patches_applied", 0)

    for file_path in all_modified:
        update_index_for_file(file_path, project_root)
        try:
            from repo_graph.repo_map_updater import update_repo_map_for_file
            update_repo_map_for_file(file_path, project_root)
        except Exception:
            pass

    return {
        "success": True,
        "output": {
            "files_modified": list(dict.fromkeys(all_modified)),
            "patches_applied": all_patches,
            "planned_changes": changes,
        },
    }


def _infra_fn(step: dict, state: AgentState) -> dict:
    """Dispatch-style: { success, output, error }; output has returncode.
    Uses step.description or step.command as shell command; defaults to 'true' if empty."""
    try:
        cmd = (step.get("description") or step.get("command") or "").strip() or "true"
        print(f"  [run_command] {cmd[:80]}{'...' if len(cmd) > 80 else ''}")
        cmd_result = run_command(cmd)
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


def _get_explain_system_prompt() -> str:
    """Load EXPLAIN system prompt from registry (Phase 13)."""
    return get_registry().get_instructions("explain_system")


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
        return {"success": False, "output": {}, "error": f"Invalid step: {e}", "classification": ResultClassification.FATAL_FAILURE.value}

    action = (step.get("action") or "EXPLAIN").upper()
    description = step.get("description") or ""
    artifact_mode = step.get("artifact_mode") or "code"
    if artifact_mode not in ("code", "docs"):
        return {
            "success": False,
            "output": {},
            "error": f"Invalid artifact_mode: {artifact_mode!r} (allowed: 'code', 'docs')",
            "classification": ResultClassification.FATAL_FAILURE.value,
        }
    state.context["artifact_mode"] = artifact_mode
    print(f"[workflow] dispatch {action}")

    current_node = state.context.get("tool_node", "START")
    allowed_tools = _tool_graph.get_allowed_tools(current_node)
    preferred_from_graph = _tool_graph.get_preferred_tool(current_node)
    chosen_tool = resolve_tool(action, allowed_tools, preferred_from_graph, current_node)
    state.context["chosen_tool"] = chosen_tool

    if action == "SEARCH_CANDIDATES":
        query = step.get("query") or step.get("description") or ""
        last_error = None
        for attempt in range(3):  # retry limit 2 + 1 initial = 3 attempts
            try:
                out = search_candidates(query, state, artifact_mode=artifact_mode)
                candidates = out.get("candidates") or []
                if candidates:
                    state.context["candidates"] = candidates
                    state.context["query"] = query
                    return {"success": True, "output": out}
                last_error = "empty results"
            except Exception as e:
                last_error = str(e)
                logger.debug("[SEARCH_CANDIDATES] attempt %d failed: %s", attempt + 1, e)
        # Fallback: grep search (Task 8) — code mode only
        if artifact_mode == "code":
            try:
                grep_out = search_code(query, tool_hint="search_for_pattern")
                results = grep_out.get("results") or []
                candidates = [
                    {"symbol": r.get("symbol", ""), "file": r.get("file", ""), "snippet": r.get("snippet", ""), "score": 0.5, "source": "grep"}
                    for r in results[:20]
                ]
                state.context["candidates"] = candidates
                state.context["query"] = query
                return {"success": True, "output": {"candidates": candidates, "fallback": "grep"}}
            except Exception as e:
                return {"success": False, "output": {}, "error": f"{last_error}; fallback grep failed: {e}"}
        return {"success": True, "output": {"candidates": [], "fallback": "none", "artifact_mode": artifact_mode}}

    if action == "BUILD_CONTEXT":
        try:
            out = build_context(candidates=None, state=state, artifact_mode=artifact_mode)
            return {"success": True, "output": out}
        except Exception as e:
            return {"success": False, "output": {}, "error": str(e)}

    if action == "SEARCH":
        if artifact_mode == "docs":
            return {
                "success": False,
                "output": {},
                "error": "SEARCH with artifact_mode='docs' is intentionally deferred in Phase 5A. "
                "Use SEARCH_CANDIDATES + BUILD_CONTEXT with artifact_mode='docs' (or EXPLAIN with artifact_mode='docs').",
                "classification": ResultClassification.RETRYABLE_FAILURE.value,
            }
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
        # If the router chose list_dir, treat INFRA as a directory listing instead of a shell command.
        # This prevents natural-language queries from being executed via run_command().
        if chosen_tool == "list_dir":
            try:
                project_root = state.context.get("project_root") or os.environ.get("SERENA_PROJECT_DIR") or os.getcwd()
                q = (step.get("description") or step.get("command") or "").strip()
                # If the planner produced a question (e.g. "where are readmes and docs"),
                # default to listing the project root.
                path = q if (q and ("/" in q or q in (".", ".."))) else "."
                root = Path(project_root)
                resolved = (root / path).resolve() if path != "." else root.resolve()
                if not resolved.is_dir():
                    resolved = root.resolve()
                entries = list_files(str(resolved))
                state.context["tool_node"] = chosen_tool
                return {
                    "success": True,
                    "output": {"path": str(resolved), "entries": entries, "returncode": 0, "router_override": "infra->list_dir"},
                }
            except Exception as e:
                # Fall back to normal INFRA policy execution if listing fails.
                logger.debug("[INFRA] list_dir override failed: %s", e)
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
        if artifact_mode == "docs":
            # Idempotency: reuse candidates when already present for this query.
            reuse = (
                (state.context.get("artifact_mode") == "docs")
                and (state.context.get("query") == query)
                and bool(state.context.get("candidates"))
            )
            if reuse:
                if state.context.get("trace_id"):
                    log_event(
                        state.context["trace_id"],
                        "docs_explain_context_reused",
                        {"artifact_mode": "docs", "reused": True},
                    )
            else:
                out = search_candidates(query, state, artifact_mode="docs")
                candidates = out.get("candidates") or []
                state.context["candidates"] = candidates
                state.context["query"] = query
            build_context(candidates=None, state=state, artifact_mode="docs")
        else:
            search_output = _search_fn(query, state)
            results = (search_output or {}).get("results") or []
            if not _is_valid_search_result(results):
                return {
                    "success": False,
                    "output": "",
                    "error": "No context for EXPLAIN. Run SEARCH first.",
                    "classification": ResultClassification.RETRYABLE_FAILURE.value,
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
                            system_prompt=_get_explain_system_prompt(),
                        )
                    else:
                        out = call_reasoning_model(
                            user_prompt,
                            system_prompt=_get_explain_system_prompt(),
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
                system_prompt=_get_explain_system_prompt(),
            )
        else:
            out = call_reasoning_model(
                user_prompt,
                system_prompt=_get_explain_system_prompt(),
                task_name="EXPLAIN",
            )
        out_str = (out or "").strip() or "[EXPLAIN: no model output]"
        print("  [model] output:", out_str[:120] + ("..." if len(out_str) > 120 else ""))
        return {"success": True, "output": out_str}
    except Exception as e:
        base = {"success": False, "output": "", "error": str(e)}
        base["classification"] = classify_result("EXPLAIN", base).value
        return base
