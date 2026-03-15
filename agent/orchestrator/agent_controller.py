"""Full agent orchestration: instruction -> plan -> retrieval -> edit -> repair -> task memory."""

import logging
import os
import time
import uuid
from pathlib import Path

from agent.execution.step_dispatcher import dispatch
from agent.memory.state import AgentState
from agent.memory.task_memory import save_task
from agent.observability.trace_logger import finish_trace, log_event, start_trace
from agent.orchestrator.plan_resolver import get_plan
from agent.orchestrator.replanner import replan
from agent.orchestrator.validator import validate_step
from config.agent_config import MAX_REPLAN_ATTEMPTS, MAX_TASK_RUNTIME_SECONDS
from config.editing_config import MAX_FILES_EDITED, MAX_PATCH_SIZE
from config.router_config import ENABLE_INSTRUCTION_ROUTER

logger = logging.getLogger(__name__)


def run_controller(
    instruction: str,
    project_root: str | None = None,
) -> dict:
    """
    Run full development workflow: plan -> retrieval -> edit -> conflict resolution
    -> patch execution -> change detection -> test repair.
    Returns task summary dict.
    """
    root = Path(project_root or os.environ.get("SERENA_PROJECT_DIR", os.getcwd())).resolve()
    task_id = str(uuid.uuid4())
    trace_id = start_trace(task_id, str(root), query=instruction)

    try:
        # Build repo map for high-level context
        try:
            from repo_graph.repo_map_builder import build_repo_map

            build_repo_map(str(root))
        except Exception as e:
            logger.debug("[agent_controller] repo_map build skipped: %s", e)

        similar_tasks: list[dict] = []
        try:
            from agent.memory.task_index import search_similar_tasks

            similar_tasks = search_similar_tasks(instruction, str(root), top_k=3)
        except Exception as e:
            logger.debug("[agent_controller] task_index search skipped: %s", e)

        plan_result = get_plan(instruction, trace_id=trace_id, log_event_fn=log_event)
        log_event(trace_id, "planner_decision", {"plan": plan_result})

        state = AgentState(
            instruction=instruction,
            current_plan=plan_result,
            context={
                "tool_node": "START",
                "retrieved_files": [],
                "retrieved_symbols": [],
                "retrieved_references": [],
                "context_snippets": [],
                "ranked_context": [],
                "context_candidates": [],
                "ranking_scores": [],
                "project_root": str(root),
                "instruction": instruction,
                "trace_id": trace_id,
                "similar_past_tasks": similar_tasks,
            },
        )

        start_time = time.perf_counter()
        completed_steps: list = []
        patches_applied: list = []
        files_modified: list = []
        errors_encountered: list = []
        replan_count = 0
        tool_calls = 0

        while not state.is_finished():
            if time.perf_counter() - start_time > MAX_TASK_RUNTIME_SECONDS:
                logger.warning("[agent_controller] max task runtime exceeded")
                errors_encountered.append("max_task_runtime_exceeded")
                log_event(trace_id, "error", {"type": "max_task_runtime_exceeded"})
                break

            step = state.next_step()
            if step is None:
                break

            step_id = step.get("id", "?")
            action = (step.get("action") or "EXPLAIN").upper()
            state.context["current_step_id"] = step_id
            logger.info("[agent_controller] step executed step_id=%s action=%s", step_id, action)

            tool_calls += 1
            if action == "EDIT":
                result = _run_edit_flow(step, state)
            else:
                result = dispatch(step, state)

            chosen_tool = state.context.get("chosen_tool", "")
            log_event(
                trace_id,
                "step_executed",
                {
                    "step_id": step_id,
                    "action": action,
                    "tool": chosen_tool,
                    "success": result.get("success", False),
                },
            )

            success = result.get("success", False)
            if success:
                completed_steps.append(step)
                out = result.get("output", {})
                if isinstance(out, dict):
                    pm = out.get("patches_applied")
                    if isinstance(pm, list):
                        patches_applied.extend(pm)
                    elif isinstance(pm, int):
                        patches_applied.append(pm)
                    files_modified.extend(out.get("files_modified", []) or [])
                    if pm or out.get("files_modified"):
                        log_event(
                            trace_id,
                            "patch_result",
                            {
                                "step_id": step_id,
                                "patches_applied": pm if isinstance(pm, int) else len(pm) if isinstance(pm, list) else 0,
                                "files_modified": out.get("files_modified", []),
                            },
                        )
            else:
                err = result.get("error", "unknown")
                errors_encountered.append(err)
                log_event(trace_id, "error", {"step_id": step_id, "action": action, "error": str(err)})
                replan_count += 1
                if replan_count >= MAX_REPLAN_ATTEMPTS:
                    logger.warning("[agent_controller] max replan attempts exceeded, stopping")
                    log_event(trace_id, "error", {"type": "max_replan_attempts_exceeded"})
                    break
                new_plan = replan(state, failed_step=step, error=result.get("error", ""))
                state.update_plan(new_plan)
                continue

            step_result = _result_to_step_result(step, result)
            valid, validation_feedback = validate_step(step, step_result, state=state)
            if not valid:
                replan_count += 1
                if replan_count >= MAX_REPLAN_ATTEMPTS:
                    logger.warning("[agent_controller] max replan attempts exceeded, stopping")
                    log_event(trace_id, "error", {"type": "max_replan_attempts_exceeded"})
                    break
                err = getattr(step_result, "error", None) or result.get("error", "")
                out_str = str(step_result.output or "")[:400] if step_result.output else ""
                error_msg = validation_feedback or str(err) or out_str or "Validation failed"
                new_plan = replan(state, failed_step=step, error=error_msg)
                state.update_plan(new_plan)
                continue

            state.record(step, step_result)
            replan_count = 0  # Reset on success so next step gets fresh replan budget

        save_task(
            task_id=task_id,
            instruction=instruction,
            plan=plan_result,
            steps=completed_steps,
            patches=patches_applied,
            files_modified=list(dict.fromkeys(files_modified)),
            errors_encountered=errors_encountered,
            results={"completed_steps": len(completed_steps)},
            project_root=str(root),
        )
        log_event(
            trace_id,
            "task_complete",
            {
                "task_id": task_id,
                "completed_steps": len(completed_steps),
                "errors": errors_encountered,
                "patches_applied": len(patches_applied),
                "files_modified": list(dict.fromkeys(files_modified)),
            },
        )
        logger.info("[agent_controller] task complete")
    except Exception as e:
        log_event(trace_id, "error", {"type": "exception", "error": str(e)})
        logger.exception("[agent_controller] task failed")
        raise
    finally:
        finish_trace(trace_id)

    # Include retrieved_symbols for session memory (Phase 6)
    retrieved_symbols: list[str] = []
    try:
        raw = state.context.get("retrieved_symbols") or []
        retrieved_symbols = [str(s) for s in raw if s]
    except Exception:
        pass

    # UX metrics (Phase 6)
    try:
        from agent.observability.ux_metrics import record_task_metrics

        had_edit = any((s.get("action") or "").upper() == "EDIT" for s in completed_steps)
        patch_success = None
        if had_edit:
            patch_success = bool(files_modified) and not errors_encountered
        record_task_metrics(
            task_id=task_id,
            interaction_latency_seconds=time.perf_counter() - start_time,
            steps_per_task=len(completed_steps),
            tool_calls=tool_calls,
            patch_success=patch_success,
            project_root=str(root),
        )
    except Exception as e:
        logger.debug("[agent_controller] ux_metrics skipped: %s", e)

    return {
        "task_id": task_id,
        "instruction": instruction,
        "completed_steps": len(completed_steps),
        "files_modified": list(dict.fromkeys(files_modified)),
        "errors": errors_encountered,
        "retrieved_symbols": list(dict.fromkeys(retrieved_symbols)),
    }


def _result_to_step_result(step: dict, result: dict):
    """Convert dispatch result to StepResult-like object for state.record."""
    from agent.memory.step_result import StepResult

    output = result.get("output", "")
    files_modified = None
    patch_size = None
    if step.get("action") == "EDIT" and isinstance(output, dict):
        files_modified = output.get("files_modified")
        patch_size = output.get("patches_applied")

    return StepResult(
        step_id=step.get("id", 0),
        action=step.get("action", "EXPLAIN"),
        success=result.get("success", False),
        output=output,
        latency_seconds=0,
        error=result.get("error"),
        files_modified=files_modified,
        patch_size=patch_size,
    )


def _run_edit_flow(step: dict, state: AgentState) -> dict:
    """Extended edit flow: plan_diff -> conflict_resolver -> patch_executor -> change_detector -> test_repair."""
    from editing.conflict_resolver import resolve_conflicts
    from editing.diff_planner import plan_diff
    from editing.patch_executor import execute_patch
    from editing.patch_generator import to_structured_patches
    from editing.test_repair_loop import run_with_repair
    from repo_graph.change_detector import RISK_HIGH, detect_change_impact
    from repo_index.indexer import update_index_for_file

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
                "error": f"max patch size exceeded",
            }

    # Change detection (before apply) for risk assessment
    edited_symbols = [(c.get("file", ""), c.get("symbol", "")) for c in changes]
    impact = detect_change_impact(edited_symbols, project_root)
    trace_id = context.get("trace_id")
    if impact.get("risk_level") == RISK_HIGH and trace_id:
        log_event(trace_id, "high_risk_edit", {"impact": impact})
        # Optional: could trigger planner verification here

    # Conflict resolution
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
        patch_plan = to_structured_patches({"changes": group}, instruction, context)
        # Use test repair loop (includes execute_patch + run_tests + repair)
        repair_result = run_with_repair(patch_plan, project_root, context, max_attempts=3)
        if not repair_result.get("success"):
            return {
                "success": False,
                "output": {
                    "error": repair_result.get("error"),
                    "reason": repair_result.get("reason"),
                },
                "error": repair_result.get("reason", repair_result.get("error")),
            }
        all_modified.extend(repair_result.get("files_modified", []))
        all_patches += repair_result.get("patches_applied", 0)

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
