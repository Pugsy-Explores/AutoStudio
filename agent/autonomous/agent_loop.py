"""
Autonomous agent loop (Mode 2): goal -> observe -> select action -> execute -> repeat.

Reuses: dispatcher, retrieval pipeline, editing pipeline, trace_logger, policy_engine.
Enforces: max_steps, max_tool_calls, max_runtime, max_edits.

When max_retries > 1, wraps with evaluator -> critic -> retry_planner meta loop.
"""

import json
import logging
import os
import time
import uuid
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FuturesTimeoutError
from pathlib import Path

from agent.autonomous.action_selector import select_next_action
from agent.autonomous.goal_manager import GoalManager
from agent.autonomous.state_observer import observe
from agent.intelligence.experience_retriever import retrieve
from agent.intelligence.repo_learning import update_from_solution as update_repo_from_solution
from agent.intelligence.solution_memory import save_solution
from agent.intelligence.task_embeddings import index_solution
from agent.intelligence.developer_model import update_from_solution as update_dev_from_solution
from agent.memory.state import AgentState
from agent.memory.step_result import StepResult
from agent.observability.trace_logger import finish_trace, log_event, start_trace
from config.agent_config import MAX_RETRY_ATTEMPTS
from config.tool_budgets import TOOL_BUDGETS

logger = logging.getLogger(__name__)


AGENT_TRACE_PATH = Path("logs/agent_trace.jsonl")


def _write_step_trace(step_id: int, tool_name: str, query: str, latency: float, result_count: int) -> None:
    """Task 20: Write step trace to logs/agent_trace.jsonl."""
    try:
        AGENT_TRACE_PATH.parent.mkdir(parents=True, exist_ok=True)
        rec = {
            "step_id": step_id,
            "tool_name": tool_name,
            "query": (query or "")[:500],
            "latency": round(latency, 3),
            "result_count": result_count,
        }
        with open(AGENT_TRACE_PATH, "a", encoding="utf-8") as f:
            f.write(json.dumps(rec) + "\n")
    except Exception as e:
        logger.debug("[agent_trace] write failed: %s", e)


def _run_dispatch_with_timeout(step: dict, state: AgentState) -> dict:
    """Run dispatch with tool budget timeout. Returns result or timeout error."""
    from agent.execution.step_dispatcher import dispatch

    action = (step.get("action") or "EXPLAIN").upper()
    timeout = TOOL_BUDGETS.get(action, 10.0)
    t0 = time.perf_counter()
    with ThreadPoolExecutor(max_workers=1) as ex:
        fut = ex.submit(dispatch, step, state)
        try:
            raw = fut.result(timeout=timeout)
            latency = time.perf_counter() - t0
            result_count = 0
            if isinstance(raw.get("output"), dict):
                out = raw["output"]
                if "candidates" in out:
                    result_count = len(out.get("candidates") or [])
                elif "context_blocks" in out:
                    result_count = len(out.get("context_blocks") or [])
                elif "results" in out:
                    result_count = len(out.get("results") or [])
            _write_step_trace(
                step_id=state.context.get("current_step_id") or 0,
                tool_name=action,
                query=step.get("query") or step.get("description") or "",
                latency=latency,
                result_count=result_count,
            )
            return raw
        except FuturesTimeoutError:
            latency = time.perf_counter() - t0
            _write_step_trace(
                step_id=state.context.get("current_step_id") or 0,
                tool_name=action,
                query=step.get("query") or step.get("description") or "",
                latency=latency,
                result_count=0,
            )
            return {
                "success": False,
                "output": {},
                "error": f"Tool timeout ({timeout}s) exceeded for {action}",
            }


def run_autonomous(
    goal: str,
    project_root: str | None = None,
    *,
    max_steps: int = 20,
    max_tool_calls: int = 50,
    max_runtime_seconds: float = 60,
    max_edits: int = 10,
    max_retries: int = MAX_RETRY_ATTEMPTS,
    success_criteria: str | None = None,
) -> dict:
    """
    Run autonomous loop: observe -> select -> dispatch -> evaluate.
    When max_retries > 1, wraps with evaluator -> critic -> retry_planner meta loop.
    Returns summary dict with goal, completed_steps, tool_calls, stop_reason, evaluation, etc.
    """
    root = Path(project_root or os.environ.get("SERENA_PROJECT_DIR", os.getcwd())).resolve()
    task_id = str(uuid.uuid4())
    trace_id = start_trace(task_id, str(root), query=goal)

    goal_manager = GoalManager(
        goal,
        max_steps=max_steps,
        max_tool_calls=max_tool_calls,
        max_runtime_seconds=max_runtime_seconds,
        max_edits=max_edits,
    )

    state = AgentState(
        instruction=goal,
        current_plan={"steps": []},
        context={
            "project_root": str(root),
            "trace_id": trace_id,
            "instruction": goal,
            "tool_node": "START",
            "retrieved_files": [],
            "retrieved_symbols": [],
            "retrieved_references": [],
            "context_snippets": [],
            "ranked_context": [],
            "context_candidates": [],
            "ranking_scores": [],
            # Phase 6A: autonomous loop is code-lane by default (no planner-derived lane).
            "dominant_artifact_mode": "code",
            "lane_violations": [],
        },
    )

    hints = retrieve(goal, str(root), trace_id=trace_id)
    state.context["experience_hints"] = hints.to_dict()

    log_event(trace_id, "autonomous_start", {"goal": goal, "limits": goal_manager.get_limits_dict(), "max_retries": max_retries})

    try:
        if max_retries <= 1:
            attempt_start = time.time()
            result, state = _run_single_attempt(goal, str(root), task_id, trace_id, goal_manager, state)
            evaluation = _evaluate_and_record(
                result,
                state,
                task_id,
                trace_id,
                0,
                success_criteria,
                str(root),
                start_time=attempt_start,
            )
            _finalize_trajectory(task_id, evaluation.status, str(root))
            if evaluation.status == "SUCCESS":
                _store_solution(task_id, goal, state, str(root))
            result["evaluation"] = evaluation.to_dict()
            return result

        # Meta loop: TrajectoryLoop handles evaluate -> critic -> retry
        from agent.meta.trajectory_loop import TrajectoryLoop

        loop = TrajectoryLoop()
        result, state, evaluation = loop.run_with_retries(
            goal,
            str(root),
            task_id,
            trace_id,
            goal_manager,
            state,
            max_retries,
            success_criteria,
        )
        if evaluation and evaluation.status == "SUCCESS":
            _store_solution(task_id, goal, state, str(root))
        return result
    finally:
        finish_trace(trace_id)


def _run_single_attempt(
    goal: str,
    root: str,
    task_id: str,
    trace_id: str,
    goal_manager: GoalManager,
    state: AgentState,
) -> tuple[dict, AgentState]:
    """Run one autonomous attempt until limits or action_selector returns None."""
    while True:
        should_stop, reason = goal_manager.should_stop()
        if should_stop:
            log_event(trace_id, "autonomous_stop", {"reason": reason, "counts": goal_manager.get_counts_dict()})
            break

        observation = observe(
            goal=goal,
            project_root=root,
            completed_steps=state.completed_steps,
            step_results=state.step_results,
            context=state.context,
        )

        step = select_next_action(observation)
        if step is None:
            log_event(trace_id, "action_selector_failed", {"observation_goal": goal})
            break

        step_id = len(state.completed_steps) + 1
        step["id"] = step.get("id") or step_id
        state.context["current_step_id"] = step_id

        goal_manager.record_tool_call()
        result_raw = _run_dispatch_with_timeout(step, state)

        success = result_raw.get("success", False)
        goal_manager.record_step(step.get("action", ""), success)

        result = _raw_to_step_result(step, result_raw)
        state.record(step, result)

        log_event(
            trace_id,
            "autonomous_step",
            {
                "step_id": step_id,
                "action": step.get("action"),
                "success": success,
                "error": result_raw.get("error"),
            },
        )

    return {
        "task_id": task_id,
        "goal": goal,
        "completed_steps": len(state.completed_steps),
        "tool_calls": goal_manager.get_counts_dict()["tool_calls"],
        "stop_reason": goal_manager.get_stop_reason() or "action_selector_failed",
        "counts": goal_manager.get_counts_dict(),
    }, state


def _evaluate_and_record(
    result: dict,
    state: AgentState,
    task_id: str,
    trace_id: str,
    attempt_num: int,
    success_criteria: str | None,
    project_root: str,
    diagnosis: dict | None = None,
    strategy: str | None = None,
    start_time: float | None = None,
):
    """Evaluate run and record to trajectory store."""
    from agent.meta.evaluator import evaluate
    from agent.meta.trajectory_store import record_attempt

    evaluation = evaluate(result, state, success_criteria=success_criteria, use_model=False)
    record_attempt(
        task_id,
        state,
        evaluation,
        diagnosis=diagnosis,
        strategy=strategy,
        project_root=project_root,
        start_time=start_time,
    )
    return evaluation


def _critic_and_plan(goal: str, state: AgentState, evaluation, trace_id: str):
    """Run critic and retry planner. Returns (diagnosis, retry_hints)."""
    from agent.meta.critic import diagnose
    from agent.meta.retry_planner import plan_retry

    diagnosis = diagnose(state, evaluation)
    retry_hints = plan_retry(goal, diagnosis)
    return diagnosis, retry_hints


def _finalize_trajectory(task_id: str, final_status: str, project_root: str) -> None:
    """Finalize trajectory store."""
    from agent.meta.trajectory_store import finalize

    finalize(task_id, final_status, project_root=project_root)


def _store_solution(task_id: str, goal: str, state: AgentState, project_root: str) -> None:
    """Store successful solution to intelligence layer (solution_memory, task_embeddings, repo_learning, developer_model)."""
    files_modified: list[str] = []
    patch_summary = "successful edit"
    for i, sr in enumerate(state.step_results or []):
        if getattr(sr, "action", "") == "EDIT":
            fm = getattr(sr, "files_modified", None)
            if fm:
                files_modified.extend(fm if isinstance(fm, list) else [fm])
            if i < len(state.completed_steps or []):
                step = state.completed_steps[i]
                if isinstance(step, dict) and step.get("description"):
                    patch_summary = step["description"][:200]
                    break
    files_modified = list(dict.fromkeys(files_modified))
    solution = {
        "task_id": task_id,
        "goal": goal,
        "files_modified": files_modified,
        "patch_summary": patch_summary,
        "success": True,
    }
    save_solution(
        task_id=task_id,
        goal=goal,
        files_modified=files_modified,
        patch_summary=patch_summary,
        success=True,
        project_root=project_root,
    )
    index_solution(task_id, goal, files_modified, patch_summary, project_root=project_root)
    update_repo_from_solution(solution, project_root)
    update_dev_from_solution(solution, project_root)


def _raw_to_step_result(step: dict, raw: dict) -> StepResult:
    """Convert dispatch result to StepResult."""
    output = raw.get("output", "")
    files_modified = None
    patch_size = None
    if step.get("action") == "EDIT" and isinstance(output, dict):
        files_modified = output.get("files_modified")
        patch_size = output.get("patches_applied")

    rc = raw.get("reason_code")
    return StepResult(
        step_id=step.get("id", 0),
        action=step.get("action", "EXPLAIN"),
        success=raw.get("success", False),
        output=output,
        latency_seconds=0,
        error=raw.get("error"),
        classification=raw.get("classification"),
        files_modified=files_modified,
        patch_size=patch_size,
        reason_code=rc if isinstance(rc, str) else None,
    )
