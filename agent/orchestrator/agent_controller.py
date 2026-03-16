"""Full agent orchestration: instruction -> plan -> retrieval -> edit -> repair -> task memory."""

import logging
import os
import time
import uuid
from pathlib import Path

from agent.memory.task_memory import save_task
from agent.meta.critic import Critic
from agent.meta.retry_planner import RetryPlanner
from agent.meta.trajectory_memory import TrajectoryMemory
from agent.observability.trace_logger import finish_trace, log_event, start_trace
from agent.orchestrator.deterministic_runner import run_deterministic
from agent.orchestrator.goal_evaluator import GoalEvaluator
from config.agent_config import MAX_AGENT_ATTEMPTS

logger = logging.getLogger(__name__)


def run_attempt_loop(
    instruction: str,
    project_root: str,
    *,
    trace_id: str,
    similar_tasks: list[dict],
    log_event_fn,
) -> tuple["AgentState", dict]:
    """
    Phase 5: attempt-level loop around the deterministic runner.
    Runs up to MAX_AGENT_ATTEMPTS; each attempt uses trajectory memory and critic feedback
    to build retry_context for the planner. Stops when goal_met or max attempts reached.
    Returns (state, loop_output) from the last run_deterministic.
    """
    from agent.memory.state import AgentState

    trajectory_memory = TrajectoryMemory()
    critic = Critic()
    retry_planner = RetryPlanner()
    goal_evaluator = GoalEvaluator()

    state: AgentState | None = None
    loop_output: dict | None = None
    retry_context: dict | None = None

    for attempt in range(MAX_AGENT_ATTEMPTS):
        log_event_fn(
            trace_id,
            "attempt_started",
            {"attempt": attempt + 1, "max_attempts": MAX_AGENT_ATTEMPTS},
        )

        state, loop_output = run_deterministic(
            instruction,
            project_root,
            trace_id=trace_id,
            similar_tasks=similar_tasks,
            log_event_fn=log_event_fn,
            retry_context=retry_context,
        )

        completed_steps = loop_output["completed_steps"]
        patches_applied = loop_output["patches_applied"]
        files_modified = loop_output["files_modified"]
        errors_encountered = loop_output["errors_encountered"]
        plan_result = loop_output["plan_result"]

        goal_met = goal_evaluator.evaluate(instruction, state)

        attempt_data = {
            "plan": plan_result,
            "step_results": state.step_results,
            "errors": errors_encountered,
            "patches_applied": patches_applied,
            "files_modified": files_modified,
            "goal_met": goal_met,
        }
        trajectory_memory.record_attempt(attempt_data)

        if goal_met:
            log_event_fn(
                trace_id,
                "attempt_success",
                {
                    "attempt": attempt + 1,
                    "completed_steps": len(completed_steps),
                    "patches_applied": patches_applied,
                    "files_modified": list(dict.fromkeys(files_modified)),
                },
            )
            return state, loop_output

        log_event_fn(
            trace_id,
            "attempt_failed",
            {
                "attempt": attempt + 1,
                "completed_steps": len(completed_steps),
                "errors": errors_encountered,
            },
        )

        if attempt >= MAX_AGENT_ATTEMPTS - 1:
            break

        critic_feedback = critic.analyze(instruction, attempt_data)
        log_event_fn(
            trace_id,
            "critic_analysis",
            {
                "attempt": attempt + 1,
                "failure_reason": critic_feedback.get("failure_reason"),
                "recommendation": critic_feedback.get("recommendation", "")[:300],
            },
        )
        summary_length = critic_feedback.get("summary_length")
        if summary_length is not None:
            log_event_fn(
                trace_id,
                "trajectory_summary_generated",
                {"attempt": attempt + 1, "summary_length": summary_length},
            )
        strategy_hint = critic_feedback.get("strategy_hint") or ""
        if strategy_hint:
            log_event_fn(
                trace_id,
                "strategy_hint_generated",
                {"attempt": attempt + 1, "strategy_hint": strategy_hint[:500]},
            )

        retry_context = retry_planner.build_retry_context(
            instruction,
            trajectory_memory,
            critic_feedback,
        )
        log_event_fn(
            trace_id,
            "attempt_retry",
            {"attempt": attempt + 1, "next_attempt": attempt + 2},
        )

    return state, loop_output


def run_controller(
    instruction: str,
    project_root: str | None = None,
    mode: str = "deterministic",
) -> dict:
    """
    Run full development workflow: plan -> retrieval -> edit -> conflict resolution
    -> patch execution -> change detection -> test repair.
    Returns task summary dict.
    """
    if mode != "deterministic":
        return _run_controller_by_mode(instruction, project_root, mode)

    root = Path(project_root or os.environ.get("SERENA_PROJECT_DIR", os.getcwd())).resolve()
    task_id = str(uuid.uuid4())
    trace_id = start_trace(task_id, str(root), query=instruction)

    try:
        # Ensure retrieval daemon is reachable (start if not running and auto-start enabled)
        try:
            from config.retrieval_config import EMBEDDING_USE_DAEMON, RERANKER_USE_DAEMON
            from agent.retrieval.daemon_ensure import ensure_retrieval_daemon

            if RERANKER_USE_DAEMON or EMBEDDING_USE_DAEMON:
                daemon_reachable = ensure_retrieval_daemon(str(root))
                log_event(
                    trace_id,
                    "retrieval_daemon_ensure",
                    {"daemon_reachable": daemon_reachable},
                )
        except Exception as e:
            logger.debug("[agent_controller] retrieval daemon ensure skipped: %s", e)

        # Build repo map for high-level context
        try:
            from repo_graph.repo_map_builder import build_repo_map

            build_repo_map(str(root))
        except Exception as e:
            logger.debug("[agent_controller] repo_map build skipped: %s", e)

        similar_tasks = []
        try:
            from agent.memory.task_index import search_similar_tasks

            similar_tasks = search_similar_tasks(instruction, str(root), top_k=3)
        except Exception as e:
            logger.debug("[agent_controller] task_index search skipped: %s", e)

        state, loop_output = run_attempt_loop(
            instruction,
            str(root),
            trace_id=trace_id,
            similar_tasks=similar_tasks,
            log_event_fn=log_event,
        )

        completed_steps = loop_output["completed_steps"]
        patches_applied = loop_output["patches_applied"]
        files_modified = loop_output["files_modified"]
        errors_encountered = loop_output["errors_encountered"]
        tool_calls = loop_output["tool_calls"]
        plan_result = loop_output["plan_result"]
        start_time = loop_output["start_time"]

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
                "patches_applied": len(patches_applied) if isinstance(patches_applied, list) else patches_applied,
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

        # Phase 4: completed_steps are (plan_id, step_id) tuples; use step_results for action.
        had_edit = any((getattr(sr, "action", "") or "").upper() == "EDIT" for sr in state.step_results)
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
        "state": state,
        "completed_steps": len(completed_steps),
        "files_modified": list(dict.fromkeys(files_modified)),
        "errors": errors_encountered,
        "retrieved_symbols": list(dict.fromkeys(retrieved_symbols)),
    }


def _run_controller_by_mode(instruction: str, project_root: str | None, mode: str) -> dict:
    """Route to autonomous or multi_agent runner. Do not move code from those loops."""
    if mode == "autonomous":
        from agent.autonomous.agent_loop import run_autonomous

        return run_autonomous(instruction, project_root=project_root)
    elif mode == "multi_agent":
        from agent.roles.supervisor_agent import run_multi_agent

        return run_multi_agent(instruction, project_root=project_root)
    else:
        raise ValueError(f"Unknown mode: {mode!r}. Use 'deterministic', 'autonomous', or 'multi_agent'.")

