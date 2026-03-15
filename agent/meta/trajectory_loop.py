"""
Trajectory loop: controlled retry with critic feedback.

Runs task attempts, evaluates results, calls critic on failure, plans retry, and retries.
Respects MAX_RETRY_ATTEMPTS and MAX_RETRY_RUNTIME_SECONDS.
"""

import logging
import time

logger = logging.getLogger(__name__)

DIVERSITY_SEQUENCE = [
    None,  # attempt 0: original (no override)
    "expand_search_scope",  # attempt 1: broaden retrieval
    "generate_new_plan",  # attempt 2: try a completely different plan
]


class TrajectoryLoop:
    """
    Retry loop: attempt -> evaluate -> critique -> retry.
    Delegates to agent_loop for execution; avoids circular imports via late import.
    """

    def run_with_retries(
        self,
        goal: str,
        project_root: str,
        task_id: str,
        trace_id: str,
        goal_manager: "GoalManager",
        state: "AgentState",
        max_retries: int,
        success_criteria: str | None,
    ) -> tuple[dict, "AgentState", "EvaluationResult"]:
        """
        Run attempts until SUCCESS or max_retries reached.
        Respects MAX_RETRY_RUNTIME_SECONDS from config.

        Returns:
            (result, state, evaluation)
        """
        from config.agent_config import MAX_RETRY_RUNTIME_SECONDS

        from agent.autonomous.agent_loop import (
            _critic_and_plan,
            _evaluate_and_record,
            _finalize_trajectory,
            _run_single_attempt,
        )
        from agent.meta.evaluator import EVALUATION_STATUS_SUCCESS
        from agent.observability.trace_logger import log_event

        evaluation = None
        diagnosis_prev = None
        strategy_prev = None
        result = None
        loop_start = time.time()

        for attempt_num in range(max_retries):
            # Enforce runtime limit before retries (always allow first attempt)
            if attempt_num > 0:
                elapsed = time.time() - loop_start
                if elapsed >= MAX_RETRY_RUNTIME_SECONDS:
                    logger.warning(
                        "[trajectory_loop] max retry runtime %.0fs exceeded, stopping",
                        MAX_RETRY_RUNTIME_SECONDS,
                    )
                    break

            attempt_start = time.time()
            result, state = _run_single_attempt(
                goal, project_root, task_id, trace_id, goal_manager, state
            )

            evaluation = _evaluate_and_record(
                result,
                state,
                task_id,
                trace_id,
                attempt_num,
                success_criteria,
                project_root,
                diagnosis=diagnosis_prev,
                strategy=strategy_prev,
                start_time=attempt_start,
            )

            trajectory_length = len(state.completed_steps or [])
            log_event(
                trace_id,
                "evaluation",
                {
                    "status": evaluation.status,
                    "attempt": attempt_num,
                    "reason": evaluation.reason,
                    "attempt_number": attempt_num,
                    "retry_strategy": strategy_prev or "",
                    "trajectory_length": trajectory_length,
                    "failure_type": (diagnosis_prev or {}).get("failure_type", ""),
                },
            )

            if evaluation.status == EVALUATION_STATUS_SUCCESS:
                break

            if attempt_num < max_retries - 1:
                diagnosis, retry_hints = _critic_and_plan(
                    goal, state, evaluation, trace_id
                )
                diagnosis_prev = diagnosis.to_dict()

                # Diversity: if critic repeats same strategy, escalate to next in sequence
                diversity_strategy = (
                    DIVERSITY_SEQUENCE[attempt_num + 1]
                    if attempt_num + 1 < len(DIVERSITY_SEQUENCE)
                    else None
                )
                if diversity_strategy and retry_hints.strategy == strategy_prev:
                    from agent.meta.retry_planner import RetryHints

                    logger.info(
                        "[trajectory_loop] strategy %r repeated; diversifying to %r",
                        retry_hints.strategy,
                        diversity_strategy,
                    )
                    retry_hints = RetryHints(
                        strategy=diversity_strategy,
                        rewrite_query=retry_hints.rewrite_query,
                        plan_override=retry_hints.plan_override,
                        retrieve_files=retry_hints.retrieve_files,
                    )
                strategy_prev = retry_hints.strategy
                state.context["retry_hints"] = retry_hints.to_dict()
                state.completed_steps = []
                state.step_results = []
                goal_manager.reset_for_retry()
                log_event(
                    trace_id,
                    "retry_prepared",
                    {
                        "strategy": retry_hints.strategy,
                        "attempt": attempt_num + 1,
                    },
                )

        if evaluation:
            _finalize_trajectory(task_id, evaluation.status, project_root)
            result["evaluation"] = evaluation.to_dict()
            result["attempts"] = attempt_num + 1

        return result, state, evaluation
