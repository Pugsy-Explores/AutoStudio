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
from agent.orchestrator.replanner import replan
from agent.orchestrator.validator import validate_step
from planner.planner import plan

logger = logging.getLogger(__name__)

MAX_FILES_EDITED = 5
MAX_PATCH_SIZE = 200
MAX_TASK_RUNTIME_SECONDS = 15 * 60  # 15 minutes


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
    trace_id = start_trace(task_id, str(root))

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

        plan_result = plan(instruction)
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

        while not state.is_finished():
            if time.perf_counter() - start_time > MAX_TASK_RUNTIME_SECONDS:
                logger.warning("[agent_controller] max task runtime exceeded")
                errors_encountered.append("max_task_runtime_exceeded")
                break

            step = state.next_step()
            if step is None:
                break

            step_id = step.get("id", "?")
            action = (step.get("action") or "EXPLAIN").upper()
            logger.info("[agent_controller] step executed step_id=%s action=%s", step_id, action)
            log_event(trace_id, "step_executed", {"step_id": step_id, "action": action})

            if action == "EDIT":
                result = _run_edit_flow(step, state)
            else:
                result = dispatch(step, state)

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
            else:
                errors_encountered.append(result.get("error", "unknown"))
                new_plan = replan(state)
                state.update_plan(new_plan)
                continue

            step_result = _result_to_step_result(step, result)
            if not validate_step(step, step_result):
                new_plan = replan(state)
                state.update_plan(new_plan)
                continue

            state.record(step, step_result)

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
        log_event(trace_id, "task_complete", {"task_id": task_id})
        logger.info("[agent_controller] task complete")
    finally:
        finish_trace(trace_id)

    return {
        "task_id": task_id,
        "instruction": instruction,
        "completed_steps": len(completed_steps),
        "files_modified": list(dict.fromkeys(files_modified)),
        "errors": errors_encountered,
    }


def _result_to_step_result(step: dict, result: dict):
    """Convert dispatch result to StepResult-like object for state.record."""
    from agent.memory.step_result import StepResult

    return StepResult(
        step_id=step.get("id", 0),
        action=step.get("action", "EXPLAIN"),
        success=result.get("success", False),
        output=result.get("output", ""),
        latency_seconds=0,
        error=result.get("error"),
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

    return {
        "success": True,
        "output": {
            "files_modified": list(dict.fromkeys(all_modified)),
            "patches_applied": all_patches,
            "planned_changes": changes,
        },
    }
