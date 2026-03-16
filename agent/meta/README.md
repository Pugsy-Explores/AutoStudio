# Meta Layer — Trajectory Loop, Critic, Retry Planner

Reflection layer for task failure: evaluates attempts, diagnoses failures, and plans retries. Used by the trajectory loop when `max_retries > 1` in autonomous mode.

## Purpose

When a task fails, the meta layer:
1. **Evaluates** whether the attempt succeeded (evaluator)
2. **Diagnoses** the failure type (critic)
3. **Plans** retry strategy (retry_planner)
4. **Retries** with the new strategy (trajectory_loop)

This prevents repeated failures with the same approach and escalates strategies (e.g. rewrite_query → expand_scope → new_plan).

## Architecture

| Module | Purpose |
|--------|---------|
| evaluator | `evaluate()` — SUCCESS/FAILURE/PARTIAL from step results |
| critic | `diagnose()` — produces Diagnosis(failure_type, affected_step, suggestion) |
| retry_planner | `plan_retry()` — maps diagnosis to RetryHints (rewrite_query, expand_scope, new_plan, etc.) |
| trajectory_loop | `run_with_retries()` — attempt → evaluate → critique → plan_retry → retry |
| trajectory_store | Persists trajectory records to `.agent_memory/trajectories/` |

## Key Classes

- `TrajectoryLoop.run_with_retries()` — main entry; runs retry cycle
- `diagnose(state, evaluation_result)` — returns Diagnosis
- `plan_retry(goal, diagnosis)` — returns RetryHints
- `evaluate()` — returns evaluation status

## Failure Types

From `critic.py`: retrieval_miss, wrong_file_localization, incorrect_patch, syntax_error_patch, test_failure, tool_error, timeout, hallucinated_api, premature_completion, hallucinated_symbol, loop_failure.

## Retry Strategies

From `retry_planner.py`: rewrite_retrieval_query, expand_search_scope, generate_new_plan, retry_edit_with_different_patch, search_symbol_dependencies.

## Integration

- **Autonomous mode:** `run_autonomous(goal, max_retries=3)` uses TrajectoryLoop when max_retries > 1
- **Deterministic mode:** No trajectory loop; replanner handles step-level failures
- Reuses dispatcher, retrieval, editing pipeline — no duplicate infrastructure

## See Also

- [dev/roadmap/phase_8_autonomous_mode.md](../../dev/roadmap/phase_8_autonomous_mode.md)
- [dev/roadmap/phase_15_trajectory.md](../../dev/roadmap/phase_15_trajectory.md)
- [Docs/AGENT_LOOP_WORKFLOW.md](../../Docs/AGENT_LOOP_WORKFLOW.md)
