"""Dispatch step actions to tool adapters. ToolGraph → Router → PolicyEngine. EXPLAIN uses model_router + chosen model."""

import json
import logging
import os
import random
import re
import yaml
from pathlib import Path

from agent.contracts.error_codes import REASON_CODE_INSUFFICIENT_SUBSTANTIVE_CONTEXT
from agent.execution.explain_gate import (
    GRAPH_PLACEHOLDER_SNIPPET_PREFIX,
    REASON_CODE_INSUFFICIENT_GROUNDING,
    code_explain_grounding_ready,
    ensure_context_before_explain,
    has_substantive_code_context,
)
from agent.prompt_system import get_registry
from config.agent_config import MAX_CONTEXT_CHARS
from agent.execution.policy_engine import ExecutionPolicyEngine, InvalidStepError, ResultClassification, _is_valid_search_result, classify_result, validate_step_input
from agent.execution.tool_graph import ToolGraph
from agent.execution.tool_graph_router import resolve_tool
from agent.memory.state import AgentState
from agent.models.model_client import call_reasoning_model, call_small_model
from agent.models.model_router import get_model_for_task
from agent.models.model_types import ModelType
from agent.observability.grounding_audit import _extract_context_tokens, _normalize_tokens
from agent.observability.trace_logger import log_event, trace_stage
from agent.retrieval import rewrite_query_with_context
from agent.retrieval.context_builder_v2 import assemble_reasoning_context
from agent.retrieval.result_contract import (
    RETRIEVAL_RESULT_TYPE_SYMBOL_BODY,
    normalize_result,
)
from agent.retrieval.retrieval_pipeline import run_retrieval_pipeline
from agent.tools import build_context, list_files, read_file, run_command, search_candidates, search_code
from config.retrieval_config import (
    ANSWER_EVAL_SAMPLE_RATE,
    ENABLE_ANSWER_EVAL,
    ENABLE_EXPLORATION,
    ENABLE_HYBRID_RETRIEVAL,
    ENABLE_VECTOR_SEARCH,
    RETRIEVAL_CACHE_SIZE,
)

logger = logging.getLogger(__name__)

_tool_graph = ToolGraph()

_DOCS_COMPATIBLE_ACTIONS = ("SEARCH_CANDIDATES", "BUILD_CONTEXT", "EXPLAIN")


def _lane_violation(state: AgentState, *, message: str, step: dict | None = None) -> dict:
    """Return a deterministic lane contract breach response (FATAL_FAILURE)."""
    trace_id = (state.context or {}).get("trace_id") if state else None
    payload = {
        "error": "lane_violation",
        "message": message,
        "dominant_artifact_mode": (state.context or {}).get("dominant_artifact_mode", "code") if state else "code",
        "step_action": ((step or {}).get("action") or "").upper() if isinstance(step, dict) else "",
        "step_artifact_mode": (step or {}).get("artifact_mode") if isinstance(step, dict) else None,
    }
    try:
        if state and isinstance(state.context, dict):
            state.context.setdefault("lane_violations", []).append(payload)
    except Exception:
        pass
    if trace_id:
        try:
            log_event(trace_id, "lane_violation", {k: v for k, v in payload.items() if k != "message"} | {"message": message[:200]})
        except Exception:
            pass
    return {
        "success": False,
        "output": {},
        "error": f"lane_violation: {message}",
        "reason_code": "lane_violation",
        "classification": ResultClassification.FATAL_FAILURE.value,
    }


def _enforce_runtime_lane_contract(step: dict, state: AgentState) -> dict | None:
    """
    Phase 6A: runtime enforcement for single-lane per task.
    Returns an error dict when violation occurs; otherwise None.
    """
    dom = (state.context or {}).get("dominant_artifact_mode", "code")
    if dom not in ("code", "docs"):
        dom = "code"
    action = (step.get("action") or "EXPLAIN").upper()

    if dom == "docs":
        # Only docs-compatible actions allowed.
        if action not in _DOCS_COMPATIBLE_ACTIONS:
            return _lane_violation(state, message=f"dominant docs lane forbids action {action!r}", step=step)
        # Docs-compatible actions must explicitly carry artifact_mode="docs" (no silent defaulting).
        if step.get("artifact_mode") != "docs":
            return _lane_violation(
                state,
                message=f"dominant docs lane requires explicit artifact_mode='docs' for action {action!r}",
                step=step,
            )
        return None

    # dom == "code": no docs steps allowed.
    if step.get("artifact_mode") == "docs":
        return _lane_violation(state, message="dominant code lane forbids artifact_mode='docs' steps", step=step)
    return None


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
                            "query": query or "",
                            "retrieval_fallback": "list_dir",
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

        # Phase 4: last-resort directory listing when all retrievers are empty (not semantic hits).
        # Policy layer treats retrieval_fallback=file_search as empty for success (Stage 43).
        retrieval_fallback_used = None
        if not (result.get("results") or []):
            try:
                root = Path(project_root or ".")
                entries = list_files(str(root))
                if entries:
                    result = {
                        "results": [
                            normalize_result(
                                {"file": str(root / e), "symbol": "", "line": 0, "snippet": e},
                                source_hint="file_search",
                            )
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

        reslist = result.get("results") or []
        if reslist:
            from agent.retrieval.search_target_filter import filter_and_rank_search_results

            _ctx = state.context or {}
            _sr = _ctx.get("source_root")
            result = {
                **result,
                "results": filter_and_rank_search_results(
                    reslist,
                    query,
                    str(project_root),
                    parent_instruction=_ctx.get("parent_instruction"),
                    extra_path_roots=(str(_sr),) if _sr else None,
                ),
            }

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
    context["project_root"] = project_root
    context["instruction"] = instruction
    # Symbol-retry hints (from mutation_strategies.symbol_retry)
    if step.get("edit_target_file_override"):
        context["edit_target_file_override"] = step["edit_target_file_override"]
    if step.get("edit_target_level"):
        context["edit_target_level"] = step["edit_target_level"]
    if step.get("edit_target_symbol_short"):
        context["edit_target_symbol_short"] = step["edit_target_symbol_short"]

    diff_plan = plan_diff(instruction, context)
    changes = diff_plan.get("changes", [])
    context["edit_target_file"] = changes[0].get("file") if changes else None
    context["edit_target_symbol"] = changes[0].get("symbol") if changes else None
    context["edit_failure_reason"] = None

    prc = context.get("prior_phase_ranked_context")
    prc_n = len(prc) if isinstance(prc, list) else 0
    rc = context.get("ranked_context")
    rc_n = len(rc) if isinstance(rc, list) else 0
    stc = context.get("search_target_candidates")
    stc_n = len(stc) if isinstance(stc, list) else 0
    rs = context.get("retrieved_symbols")
    rs_n = len(rs) if isinstance(rs, list) else 0
    rm = context.get("retrieval_metrics") or {}
    _samples: list[str] = []
    for key in ("ranked_context", "prior_phase_ranked_context"):
        for item in (context.get(key) or [])[:12]:
            if isinstance(item, dict) and item.get("file"):
                fp = str(item.get("file", "")).strip()
                if fp and fp not in _samples:
                    _samples.append(fp)
    context["edit_grounding_telemetry"] = {
        "ranked_context_items": rc_n,
        "prior_phase_ranked_items": prc_n,
        "search_target_candidates": stc_n,
        "retrieved_symbols_items": rs_n,
        "plan_diff_changes": len(changes),
        "instruction_path_injects": rm.get("instruction_path_injects"),
        "context_file_sample": _samples[:6],
    }

    if changes:
        root = Path(project_root).resolve()
        for c in changes[:2]:
            fp = (c.get("file") or "").strip()
            if not fp:
                continue
            p = Path(fp)
            if not p.is_absolute():
                p = root / fp
            try:
                p = p.resolve()
            except OSError:
                context["edit_failure_reason"] = "patch_anchor_not_found"
                return {
                    "success": False,
                    "output": {"failure_reason_code": "patch_anchor_not_found", "planned_changes": changes},
                    "error": "edit target path invalid",
                }
            try:
                p.relative_to(root)
            except ValueError:
                context["edit_failure_reason"] = "patch_anchor_not_found"
                return {
                    "success": False,
                    "output": {"failure_reason_code": "patch_anchor_not_found", "planned_changes": changes},
                    "error": "edit target outside project root",
                }
            if not p.exists():
                context["edit_failure_reason"] = "patch_anchor_not_found"
                return {
                    "success": False,
                    "output": {"failure_reason_code": "patch_anchor_not_found", "planned_changes": changes},
                    "error": f"edit target not found: {p}",
                }
            if p.is_dir():
                context["edit_failure_reason"] = "target_is_directory"
                return {
                    "success": False,
                    "output": {"failure_reason_code": "target_is_directory", "planned_changes": changes},
                    "error": f"edit target is a directory: {p}",
                }

    if not changes:
        context["edit_failure_reason"] = "empty_patch"
        return {
            "success": False,
            "output": {"planned_changes": [], "failure_reason_code": "empty_patch"},
            "error": "no_changes_planned",
        }

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
            fr = loop_result.get("failure_reason_code") or loop_result.get("failure_type")
            context["edit_failure_reason"] = fr
            return {
                "success": False,
                "output": {
                    "error": loop_result.get("error"),
                    "reason": loop_result.get("reason"),
                    "failure_reason_code": fr,
                },
                "error": loop_result.get("reason", loop_result.get("error")),
            }
        all_modified.extend(loop_result.get("files_modified", []))
        all_patches += loop_result.get("patches_applied", 0)

    context["edit_failure_reason"] = None
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


def _write_artifact_fn(step: dict, state: AgentState) -> dict:
    """Write previous EXPLAIN output to artifact_path. Stage 16: explain-artifact tasks."""
    path = step.get("artifact_path") or ""
    if not path or not isinstance(path, str):
        return {
            "success": False,
            "output": {},
            "error": "WRITE_ARTIFACT requires artifact_path",
            "classification": ResultClassification.FATAL_FAILURE.value,
        }
    project_root = (state.context or {}).get("project_root") or os.environ.get("SERENA_PROJECT_DIR") or os.getcwd()
    content = ""
    for sr in reversed(state.step_results or []):
        if getattr(sr, "action", "").upper() == "EXPLAIN" and getattr(sr, "success", False):
            out = getattr(sr, "output", "")
            content = out if isinstance(out, str) else str(out or "")
            break
    if not content:
        return {
            "success": False,
            "output": {},
            "error": "WRITE_ARTIFACT: no prior EXPLAIN output to write",
            "classification": ResultClassification.RETRYABLE_FAILURE.value,
        }
    full_path = Path(project_root) / path
    try:
        full_path.parent.mkdir(parents=True, exist_ok=True)
        full_path.write_text(content, encoding="utf-8")
        rel_path = str(path)
        return {
            "success": True,
            "output": {"files_modified": [rel_path], "path": rel_path},
            "classification": ResultClassification.SUCCESS.value,
        }
    except Exception as e:
        return {
            "success": False,
            "output": {},
            "error": f"WRITE_ARTIFACT failed: {e}",
            "classification": ResultClassification.RETRYABLE_FAILURE.value,
        }


# --- Controlled exploration (after bundle selector) ---
MAX_EXPLORATION_TOTAL_ROWS = 8
MAX_EXPLORATION_ADDED_ROWS = 3
MAX_EXPLORATION_STEPS = 2
MAX_FRONTIER_SIZE = 4


def _rank_exploration_seeds(selected: list[dict]) -> list[dict]:
    """Rank seeds for exploration: prefer linked, impl-backed, richer relations."""
    return sorted(
        selected,
        key=lambda r: (
            bool(r.get("relations")),
            bool(r.get("implementation_body_present", False)),
            len(r.get("relations") or []),
        ),
        reverse=True,
    )


def _compute_structure(rows: list[dict]) -> int:
    """Structure score: linked_count + distinct_files."""
    linked = sum(1 for r in rows if r.get("relations"))
    files = len(set(r.get("file") for r in rows if r.get("file")))
    return linked + files


def _linked_count(rows: list[dict]) -> int:
    """Count rows with non-empty relations."""
    return sum(1 for r in rows if isinstance(r.get("relations"), list) and r.get("relations"))


def _is_useful_row(r: dict) -> bool:
    """Filter: keep only rows with relations or implementation body (avoids noise accumulation)."""
    return bool(r.get("relations")) or bool(r.get("implementation_body_present"))


def _norm_path_key(p: str | None) -> str:
    """Normalize file path for set membership."""
    return str(p or "").replace("\\", "/").strip().lower()


def _diverse_frontier(frontier_cids: list[str], pool_by_id: dict[str, dict]) -> list[str]:
    """Prefer distinct files in frontier (avoids same-file tunnel vision); pad to MAX_FRONTIER_SIZE."""
    seen_files: set[str] = set()
    out: list[str] = []
    for cid in frontier_cids:
        row = pool_by_id.get(cid)
        if not row:
            continue
        f = _norm_path_key(row.get("file"))
        if f and f not in seen_files:
            out.append(cid)
            seen_files.add(f)
        if len(out) >= MAX_FRONTIER_SIZE:
            break
    if len(out) < MAX_FRONTIER_SIZE:
        for cid in frontier_cids:
            if cid in out:
                continue
            if pool_by_id.get(cid):
                out.append(cid)
            if len(out) >= MAX_FRONTIER_SIZE:
                break
    return out


def _rank_new_rows(rows: list[dict]) -> list[dict]:
    """Prefer shallow (depth 1) over deep; then relations, then impl. Most useful info is 1-hop."""
    return sorted(
        rows,
        key=lambda r: (
            r.get("exploration_depth", 99),  # prefer depth 1 (ascending = shallow first)
            not bool(r.get("relations")),  # relations before non-relations
            not bool(r.get("implementation_body_present", False)),  # impl before non-impl
        ),
    )


def _skip_redundant_same_file(r: dict, selected_files: set[str]) -> bool:
    """Skip rows in already-selected files with no relations (structural duplication)."""
    fp = _norm_path_key(r.get("file"))
    if not fp or fp not in selected_files:
        return False
    return not r.get("relations")


def _should_run_exploration(state: AgentState) -> bool:
    """True when exploration should run: code EXPLAIN, bundle selector used, architecture intent, low linked/impl."""
    ctx = state.context or {}
    mode = ctx.get("artifact_mode") or ctx.get("dominant_artifact_mode") or "code"
    if mode != "code":
        return False
    if not ctx.get("bundle_selector_used"):
        return False
    intent = ctx.get("retrieval_intent") or ""
    if intent != "architecture":
        return False
    linked = ctx.get("bundle_selector_selected_linked_row_count", 0)
    impl = ctx.get("bundle_selector_selected_impl_body_count", 0)
    if linked >= 2 and impl >= 1:
        return False
    return True


def _run_exploration(state: AgentState) -> None:
    """
    Chain-aware graph exploration: step 1 = neighbors, step 2 = neighbors-of-neighbors.
    Uses bridge-first ranked seeds, deterministic tool choice (relations > impl > file_region),
    filters to useful rows (relations or impl), and preserves path continuity (parent_id, depth).
    """
    from agent.retrieval.exploration_tools import expand_from_node

    ctx = state.context or {}
    pool = ctx.get("retrieval_candidate_pool") or []
    selected = ctx.get("bundle_selector_selected_pool") or []

    if not selected or not pool:
        return

    pool_ids = {str(r.get("candidate_id", "")) for r in pool if r.get("candidate_id")}
    pool_by_id = {str(r.get("candidate_id", "")): r for r in pool if r.get("candidate_id")}
    selected_ids = {str(r.get("candidate_id", "")) for r in selected if r.get("candidate_id")}
    selected_files = {_norm_path_key(r.get("file")) for r in selected if r.get("file")}

    ranked_seeds = _rank_exploration_seeds(selected)
    bridge_candidates = [r for r in selected if r.get("is_bridge")]
    bridge_ids = {str(r.get("candidate_id", "")) for r in bridge_candidates if r.get("candidate_id")}
    ranked_non_bridge = [r for r in ranked_seeds if str(r.get("candidate_id", "")) not in bridge_ids]
    seeds = bridge_candidates + ranked_non_bridge

    linked_before = _linked_count(selected)
    before_structure = _compute_structure(selected)

    new_rows: list[dict] = []
    seen_ids = set(selected_ids)
    actual_steps = 0
    frontier = [str(r.get("candidate_id", "")) for r in seeds if r.get("candidate_id")]

    for step_idx in range(MAX_EXPLORATION_STEPS):
        if not frontier or len(new_rows) >= MAX_EXPLORATION_ADDED_ROWS:
            break
        frontier = _diverse_frontier(frontier, pool_by_id)
        step_candidates: list[dict] = []
        next_frontier: list[str] = []
        for cid in frontier:
            seed_row = pool_by_id.get(cid)
            if not seed_row:
                continue
            results = expand_from_node(cid, pool, seed_row)
            for r in results:
                rid = str(r.get("candidate_id", ""))
                if not rid or rid in seen_ids or rid not in pool_ids:
                    continue
                if not _is_useful_row(r):
                    continue
                if _skip_redundant_same_file(r, selected_files):
                    continue
                r_copy = dict(r)
                r_copy["exploration_parent_id"] = cid
                r_copy["exploration_depth"] = step_idx + 1
                step_candidates.append(r_copy)
                next_frontier.append(rid)
        ranked = _rank_new_rows(step_candidates)
        for r_copy in ranked:
            if len(new_rows) >= MAX_EXPLORATION_ADDED_ROWS:
                break
            rid = str(r_copy.get("candidate_id", ""))
            if rid in seen_ids:
                continue
            parent_id = str(r_copy.get("exploration_parent_id", ""))
            if parent_id and parent_id not in seen_ids:
                parent_row = pool_by_id.get(parent_id)
                if parent_row and _is_useful_row(parent_row):
                    p_copy = dict(parent_row)
                    p_copy["exploration_parent_id"] = ""
                    p_copy["exploration_depth"] = step_idx
                    new_rows.append(p_copy)
                    seen_ids.add(parent_id)
                    if len(new_rows) >= MAX_EXPLORATION_ADDED_ROWS:
                        break
            new_rows.append(r_copy)
            seen_ids.add(rid)
        actual_steps += 1
        after_structure = _compute_structure(selected + new_rows)
        if after_structure <= before_structure and step_idx > 0:
            break
        before_structure = after_structure
        frontier = next_frontier

    new_rows = new_rows[:MAX_EXPLORATION_ADDED_ROWS]
    combined = selected + new_rows
    linked_after = _linked_count(combined)
    structure_gain = _compute_structure(combined) - _compute_structure(selected)

    before_tokens = _extract_context_tokens(selected)
    after_tokens = _extract_context_tokens(combined)
    new_tokens = after_tokens - before_tokens

    ctx["ranked_context"] = combined[:MAX_EXPLORATION_TOTAL_ROWS]
    ctx["exploration_used"] = True
    ctx["exploration_added_count"] = len(new_rows)
    ctx["exploration_structure_gain"] = structure_gain
    ctx["exploration_steps_used"] = actual_steps
    ctx["exploration_helped"] = structure_gain > 0
    ctx["exploration_improved_structure"] = structure_gain > 0
    ctx["exploration_linked_gain"] = linked_after - linked_before
    ctx["exploration_debug"] = {
        "used": True,
        "added_count": len(new_rows),
        "new_token_count": len(new_tokens),
        "exploration_new_tokens": list(new_tokens),
    }
    if structure_gain > 0:
        ctx["bundle_selector_post_exploration_hint"] = True
    logger.debug(
        "[exploration] added %d rows, steps=%d, structure_gain=%d, linked_gain=%d, total %d",
        len(new_rows), actual_steps, structure_gain, linked_after - linked_before, len(ctx["ranked_context"]),
    )


def _apply_grounding_and_exploration_audit(state: AgentState, answer_text: str) -> None:
    """Compute grounding_debug (overlap-based) and update exploration_debug with used_new_tokens."""
    ctx = state.context or {}
    final_rows = [r for r in (ctx.get("ranked_context") or []) if isinstance(r, dict)]
    answer_tokens = _normalize_tokens(answer_text)
    context_tokens = _extract_context_tokens(final_rows)
    overlap = answer_tokens.intersection(context_tokens)
    overlap_score = len(overlap) / max(len(answer_tokens), 1)
    ctx["grounding_debug"] = {
        "overlap_score": overlap_score,
        "overlap_count": len(overlap),
    }
    exploration_debug = dict(ctx.get("exploration_debug") or {})
    new_tokens_set = set(exploration_debug.get("exploration_new_tokens") or [])
    used_new_tokens = new_tokens_set & answer_tokens
    exploration_debug["used_new_token_count"] = len(used_new_tokens)
    exploration_debug["exploration_effective"] = len(used_new_tokens) > 0
    exploration_debug.pop("exploration_new_tokens", None)
    ctx["exploration_debug"] = exploration_debug


def _run_answer_grounding_evaluation(state: AgentState, answer_text: str) -> None:
    """Post-EXPLAIN evaluation: is the answer supported by retrieved context? Pure observability; fail-safe."""
    if not ENABLE_ANSWER_EVAL:
        return
    if random.random() > ANSWER_EVAL_SAMPLE_RATE:
        return

    ctx = state.context or {}
    selected = ctx.get("bundle_selector_selected_pool") or []
    fallback = ctx.get("ranked_context") or []
    rows = selected if selected else fallback
    rows_for_eval = rows[:6]

    instruction = getattr(state, "instruction", "") or ""

    context_snippets = []
    for r in rows_for_eval:
        if not isinstance(r, dict):
            continue
        snippet = r.get("snippet") or r.get("content") or ""
        if snippet:
            context_snippets.append(str(snippet)[:500])
    context_text = "\n\n".join(context_snippets)

    try:
        prompt_dir = Path(__file__).resolve().parent.parent / "prompt_versions" / "evaluation"
        prompt_path = prompt_dir / "answer_grounding_v1.yaml"
        if not prompt_path.exists():
            ctx["answer_grounding_eval"] = {"error": "prompt_not_found", "supported": None}
            return
        with open(prompt_path, encoding="utf-8") as f:
            data = yaml.safe_load(f) or {}
        system_text = (data.get("system") or "").strip()
        user_tpl = (data.get("user") or "").strip()
        user_prompt = user_tpl.format(
            instruction=instruction[:1000],
            answer=(answer_text or "")[:3000],
            context=context_text[:4000] or "(none)",
        )

        result = call_small_model(
            user_prompt,
            task_name="evaluation",
            system_prompt=system_text,
            max_tokens=600,
        )

        s = (result or "").strip()
        m = re.search(r"```(?:json)?\s*([\s\S]*?)\s*```", s)
        if m:
            s = m.group(1).strip()
        parsed = json.loads(s) if s else None

        if parsed and isinstance(parsed, dict):
            ctx["answer_grounding_eval"] = {
                "supported": parsed.get("supported"),
                "support_strength": parsed.get("support_strength"),
                "missing_evidence": parsed.get("missing_evidence"),
                "context_row_count": len(rows_for_eval),
            }
        else:
            ctx["answer_grounding_eval"] = {"error": "parse_failed", "supported": None}
    except Exception as e:
        ctx["answer_grounding_eval"] = {"error": str(e)[:200], "supported": None}


def _shape_query_for_explain_retrieval(instruction: str) -> str | None:
    """
    Extract focused code-explanation target from compound requests.
    Used only on first EXPLAIN retrieval (code lane) to avoid mixed-context from broad queries.
    Returns None when no extraction; caller falls back to original instruction.
    Deterministic heuristics only.
    """
    if not instruction or not isinstance(instruction, str):
        return None
    t = instruction.strip()
    if not t:
        return None
    generic = {"flow", "architecture", "docs", "documentation", "work", "works", "the", "a", "an", "how"}

    # "explain how X ..." -> extract X (e.g. "explain how replanner preserves dominant lane" -> "replanner")
    m = re.search(r"\bexplain\s+how\s+([a-zA-Z_][a-zA-Z0-9_]*)", t, re.IGNORECASE)
    if m:
        return m.group(1)

    # "explain X" or "explain X flow" or "explain X ..."
    m = re.search(r"\bexplain\s+([a-zA-Z_][a-zA-Z0-9_]*(?:\s+[a-zA-Z_][a-zA-Z0-9_]*)*)", t, re.IGNORECASE)
    if m:
        raw = m.group(1).strip()
        tokens = raw.split()
        for tok in tokens:
            if tok.lower() not in generic and len(tok) >= 2:
                return tok
        return tokens[0] if tokens else None

    # "how X works"
    m = re.search(r"\bhow\s+([a-zA-Z_][a-zA-Z0-9_]*)\s+works", t, re.IGNORECASE)
    if m:
        return m.group(1)

    # "X flow" (standalone phrase)
    m = re.search(r"\b([a-zA-Z_][a-zA-Z0-9_]*)\s+flow\b", t, re.IGNORECASE)
    if m:
        return m.group(1)

    return None


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


def _filter_stub_placeholders_when_impl_exists(ranked_context: list[dict]) -> list[dict]:
    """
    When impl-body snippets exist, filter out stub-only placeholders so they
    do not crowd out real code in the 8000-char budget.
    Preserves original order. Does not reorder.
    """
    has_impl = any(
        c.get("implementation_body_present")
        or c.get("retrieval_result_type") == RETRIEVAL_RESULT_TYPE_SYMBOL_BODY
        for c in ranked_context
        if isinstance(c, dict)
    )
    if not has_impl:
        return ranked_context
    return [
        c
        for c in ranked_context
        if not isinstance(c, dict)
        or not (c.get("snippet") or "").strip().startswith(GRAPH_PLACEHOLDER_SNIPPET_PREFIX)
    ]


def _format_explain_context(state: AgentState) -> str:
    """Format structured context for EXPLAIN prompt. Prefer ranked_context; fallback to search_memory.
    Uses anchored blocks (FILE/SYMBOL/LINES/SNIPPET) for clear file associations.
    When impl-body exists, filters out stub placeholders to avoid crowding (fixes Replan 1 bug)."""
    parts = []
    ranked_context = state.context.get("ranked_context") or []
    if ranked_context:
        filtered = _filter_stub_placeholders_when_impl_exists(ranked_context)
        if filtered and not any(
            isinstance(r, dict) and (r.get("retrieval_result_type") or r.get("candidate_kind"))
            for r in filtered
        ):
            logger.warning(
                "[step_dispatcher] EXPLAIN ranked_context lacks retrieval_result_type/candidate_kind "
                "on all rows (typed grounding signal weak)"
            )
        assembled = assemble_reasoning_context(filtered, max_chars=8000)
        if assembled:
            parts.append("--- BEGIN CONTEXT ---")
            parts.append(assembled.strip())
            parts.append("--- END CONTEXT ---")
            logger.info("[context_anchor] using %d anchored snippets", len(filtered))
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
    # Phase 6A: runtime lane contract enforcement (dominant lane is source of truth).
    lane_err = _enforce_runtime_lane_contract(step, state)
    if lane_err is not None:
        return lane_err
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
                "reason_code": "lane_violation",
                "classification": ResultClassification.RETRYABLE_FAILURE.value,
            }
        raw = _policy_engine.execute_with_policy(step, state)
        state.context["tool_node"] = chosen_tool
        if raw.get("success") and raw.get("output"):
            out = raw["output"]
            # Always run pipeline (including empty results) so docs-alignment instruction-path
            # injection and empty-search recovery can populate ranked_context / search_target_candidates.
            if isinstance(out, dict):
                run_retrieval_pipeline(out.get("results") or [], state, out.get("query"))
            cand: list[str] = []
            for item in state.context.get("ranked_context") or []:
                if isinstance(item, dict):
                    f = (item.get("file") or "").strip()
                    if f and f not in cand:
                        cand.append(f)
            state.context["search_target_candidates"] = cand[:40]
            # SEARCH Quality Audit (env-gated): evaluate query quality, log to trace
            try:
                from agent.eval.search_quality_audit import run_audit_after_search

                search_query = (out.get("query") or step.get("description") or "") if isinstance(out, dict) else ""
                results_list = (out.get("results") or []) if isinstance(out, dict) else []
                top_files = [str(r.get("file", "")) for r in results_list[:5] if isinstance(r, dict) and r.get("file")]
                run_audit_after_search(
                    instruction=getattr(state, "instruction", "") or "",
                    search_description=search_query,
                    ranked_context=state.context.get("ranked_context") or [],
                    results_count=len(results_list),
                    top_files=top_files,
                    step_results=state.step_results,
                    trace_id=state.context.get("trace_id"),
                )
            except Exception as e:
                logger.debug("[SEARCH] search_quality_audit skipped: %s", e)
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

    if action == "WRITE_ARTIFACT":
        return _write_artifact_fn(step, state)

    # EXPLAIN or unknown: use config task_models["EXPLAIN"] then call chosen model
    state.context["tool_node"] = chosen_tool

    def _code_explain_substantive_fail_response() -> dict:
        """Stage 47: ranked_context has rows but none are usable for code-lane EXPLAIN."""
        return {
            "success": False,
            "output": "",
            "error": (
                "EXPLAIN received non-substantive context. "
                "Add SEARCH for implementation code or rebuild context with source snippets."
            ),
            "reason_code": REASON_CODE_INSUFFICIENT_SUBSTANTIVE_CONTEXT,
            "classification": ResultClassification.RETRYABLE_FAILURE.value,
        }

    # 1) Substantive gate (code lane): fail fast if context exists but is junk.
    if artifact_mode == "code":
        ranked_for_explain = state.context.get("ranked_context") or []
        if ranked_for_explain and not has_substantive_code_context(ranked_for_explain):
            return _code_explain_substantive_fail_response()

    # Context gate: avoid LLM call when ranked_context is empty
    has_context, _ = ensure_context_before_explain(step, state)
    if not has_context:
        logger.info("[context_gate] explain requested without context -> injecting SEARCH")
        base = step.get("query") or step.get("description") or ""
        if artifact_mode == "docs":
            query = base
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
            # Code lane: use query if present; else apply shaping to description.
            if step.get("query"):
                query = base
            else:
                shaped = _shape_query_for_explain_retrieval(base)
                query = shaped if shaped else base
            search_output = _search_fn(query, state)
            results = (search_output or {}).get("results") or []
            if not _is_valid_search_result(results, search_output if isinstance(search_output, dict) else None):
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

    # 2) Substantive gate again after inject (code lane): inject may have populated ranked_context.
    if artifact_mode == "code":
        ranked_after_inject = state.context.get("ranked_context") or []
        if ranked_after_inject and not has_substantive_code_context(ranked_after_inject):
            return _code_explain_substantive_fail_response()

    # 2b) Bundle selector pass (code EXPLAIN, architecture-style, when flag on)
    if artifact_mode == "code":
        state.context["final_answer_context_from_selected_rows_only"] = False
        try:
            from agent.retrieval.bundle_selector import run_bundle_selector, should_use_bundle_selector

            ranked_for_selector = state.context.get("ranked_context") or []
            if should_use_bundle_selector(step, state, ranked_for_selector):
                run_bundle_selector(step, state)  # fail-soft: leaves ranked_context unchanged on failure

            # 2c) Controlled exploration pass (optional, after bundle selector)
            if ENABLE_EXPLORATION and _should_run_exploration(state):
                try:
                    _run_exploration(state)
                except Exception as e:
                    logger.debug("[step_dispatcher] exploration skipped: %s", e)
            # Search debug: update last record with post-selector final context
            _records = state.context.get("search_debug_records") or []
            if _records:
                _rec = _records[-1]
                _final = state.context.get("ranked_context") or []
                _rec["final_count"] = len(_final)
                _rec["final_files"] = [str(r.get("file", "")) for r in _final[:10] if isinstance(r, dict) and r.get("file")]
                _rec["final_has_impl"] = any(isinstance(r, dict) and r.get("implementation_body_present") for r in _final)
                _rec["final_has_linked"] = any(
                    isinstance(r, dict) and isinstance(r.get("relations"), list) and r.get("relations")
                    for r in _final
                )
                _rec["selector_used"] = bool(state.context.get("bundle_selector_used"))
                _rec["final_has_signal"] = _rec["final_has_impl"] or _rec["final_has_linked"]
                _rec["selection_loss"] = _rec.get("pool_has_signal", False) and not _rec["final_has_signal"]
        except Exception as e:
            logger.debug("[step_dispatcher] bundle selector skipped: %s", e)
            state.context.setdefault("bundle_selector_skip_reason", "error")
        if not state.context.get("exploration_used"):
            state.context["exploration_debug"] = {
                "used": False,
                "added_count": 0,
                "new_token_count": 0,
                "used_new_token_count": 0,
                "exploration_effective": False,
            }

    trace_id = state.context.get("trace_id")
    step_id = state.context.get("current_step_id")
    try:
        # 3) Grounding readiness (code lane): typed/heuristic sufficiency — distinct from Stage 47.
        if artifact_mode == "code":
            ready, signals = code_explain_grounding_ready(step, state)
        else:
            ready, signals = True, {}
        if trace_id and artifact_mode == "code":
            log_event(
                trace_id,
                "explain_grounding_check",
                {
                    "step_id": step_id,
                    "ready": bool(ready),
                    "reason_code": (signals or {}).get("reason_code"),
                    "signals": signals,
                },
            )
        if not ready:
            # Return a "successful execution" so the validator can treat this as a semantic/contract failure
            # and route to replanning without same-step retries in AGENT mode.
            reason_code = (signals or {}).get("reason_code") or REASON_CODE_INSUFFICIENT_GROUNDING
            return {
                "success": True,
                "output": "",
                "error": "EXPLAIN blocked: insufficient grounding evidence (improve SEARCH/BUILD_CONTEXT first).",
                "reason_code": reason_code,
                "classification": ResultClassification.SUCCESS.value,
            }

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
                    _apply_grounding_and_exploration_audit(state, out_str)
                    _run_answer_grounding_evaluation(state, out_str)
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
        _apply_grounding_and_exploration_audit(state, out_str)
        _run_answer_grounding_evaluation(state, out_str)
        return {"success": True, "output": out_str}
    except Exception as e:
        base = {"success": False, "output": "", "error": str(e)}
        base["classification"] = classify_result("EXPLAIN", base).value
        return base
