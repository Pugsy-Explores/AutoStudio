# Metrics

<!-- Phase 3/4 evaluation. Run: python scripts/run_principal_engineer_suite.py --scenarios (add --use-agent-loop for Phase 4 metrics) -->
<!-- Phase 5 capability eval. Run: python scripts/run_capability_eval.py (--mock for CI, --limit N for quick test) -->

| ID | Task | task_success | retrieval_success | edit_success | latency | Notes |
|----|------|--------------|-------------------|--------------|---------|------|
| G1-001 | Explain what AgentState contains and how it is used in the agent loop. | | | | | |
| G1-002 | Explain how StepExecutor works. | | | | | |
| G1-003 | Explain what the retrieval_pipeline does and its stage order. | | | | | |
| G1-004 | Explain what context_ranker does. | | | | | |
| G1-005 | Explain what the replanner does when a step fails. | | | | | |
| G2-001 | Find where the patch validator is implemented. | | | | | |
| G2-002 | Find all callers of execute_step. | | | | | |
| G2-003 | Locate the symbol graph builder. | | | | | |
| G2-004 | Find where planner steps are validated. | | | | | |
| G2-005 | Find where retry logic exists in the codebase. | | | | | |
| G3-001 | Add logging to the execute_step function in agent/execution/executor.py. | | | | | |
| G3-002 | Add a docstring to the load_dataset function in planner/planner_eval.py if it exists. | | | | | |
| G3-003 | Rename a local variable for clarity in one function in agent/execution/step_dispatcher.py. | | | | | |
| G3-004 | Fix any missing import in tests/conftest.py if one exists. | | | | | |
| G3-005 | Add a type hint to the route_instruction function return type in config/router_config.py. | | | | | |
| G4-001 | Add input validation to the dispatch function in agent/execution/step_dispatcher.py. | | | | | |
| G4-002 | Add a try/except block around the main logic in agent/execution/executor.py execute_step. | | | | | |
| G4-003 | Add debug logging to the policy engine execute_with_policy method. | | | | | |
| G4-004 | Add retry limit configuration to the repair loop if it exists. | | | | | |
| G4-005 | Add error handling for empty context in the context builder. | | | | | |
| G5-001 | Add logging to all executor classes in agent/execution. | | | | | |
| G5-002 | Update the run_agent function signature across agent/__main__.py and agent/cli/run_agent.py to be consistent. | | | | | |
| G5-003 | Add a config constant for max retry limit in config/router_config.py and use it in any retry logic. | | | | | |
| G5-004 | Add retry limit to all classes that have retry logic. | | | | | |
| G5-005 | Add type hints to the agent/memory/state.py module. | | | | | |
| G6-001 | Fix any missing import in tests/conftest.py if one exists. | | | | | |
| G6-002 | Fix incorrect return value in config/router_config.py route_instruction. | | | | | |
| G6-003 | Fix the failing test in tests/test_planner_eval.py if any test is broken. | | | | | |
| G6-004 | Fix edge case where context builder receives empty ranked context and crashes. | | | | | |
| G6-005 | Fix patch executor to handle malformed diff gracefully. | | | | | |
| G7-001 | Add retry limit constant to config and use it in policy_engine. | | | | | |
| G7-002 | Add error handling for empty context in the context builder. | | | | | |
| G7-003 | Add a CLI flag --limit to scripts/replay_trace.py. | | | | | |
| G7-004 | Add config validation for MAX_STEPS in config/agent_config.py. | | | | | |
| G7-005 | Add retry limit to the repair loop in editing if it exists. | | | | | |
| G8-001 | Extract the plan validation logic from planner_eval into a separate function. | | | | | |
| G8-002 | Extract trace stage logging into a helper in trace_logger. | | | | | |
| G8-003 | Rename classify_failure to classify_step_failure in policy_engine. | | | | | |
| G8-004 | Move the action normalization logic from planner_eval into a shared utility. | | | | | |
| G8-005 | Refactor replanner to use configurable MAX_REPLAN constant from config. | | | | | |

**Key metrics:**
- task_success_rate
- retrieval_recall
- edit_success_rate
- mean_latency_sec

**Phase 4 latency buckets (target: <10s avg task runtime):**
- retrieval_latency: time in retrieval pipeline
- planner_latency: time in planner
- editing_latency: time in patch execution
- total_runtime: wall-clock per task

**Phase 4 reliability metrics:**
- retry_rate: % of steps that required retry
- replan_rate: % of tasks that triggered replan
- failure_rate: % of tasks that failed
- latency_avg: average task runtime (seconds)

**Phase 5 capability metrics** (run_capability_eval.py, dataset: tests/dev_tasks.json):
- task_success_rate
- retrieval_recall
- planner_accuracy
- edit_success_rate
- avg_latency
- avg_files_modified
- avg_steps_per_task
- avg_patch_size

Report output: `reports/eval_report.json`

**Phase 8 reflection metrics** (run_autonomous_eval.py, dataset: tests/autonomous_tasks.json):
- attempts_per_goal — mean attempts to succeed
- retry_success_rate — % of FAILURE→SUCCESS across retries
- critic_accuracy — % of diagnoses that led to a successful next attempt
- trajectory_reuse — % of runs where a past trajectory was consulted
- autonomous_success_rate — % of tasks that achieved SUCCESS

Report output: `reports/autonomous_eval_report.json`

**Phase 9 multi-agent metrics** (run_multi_agent_eval.py, dataset: tests/multi_agent_tasks.json):
- agent_delegations — mean agents_used per task
- retry_depth — mean patch_attempts before success/fail
- critic_accuracy — % of critic runs that led to success on next attempt
- localization_accuracy — % of tasks with non-empty candidate_files
- patch_success_rate — % of edit steps that succeeded
- goal_success_rate — % of tasks where goal_success=True

Report output: `reports/multi_agent_eval_report.json`; `--merge` merges into `reports/eval_report.json`

**Phase 10 repository metrics** (run_repository_eval.py, dataset: tests/repository_tasks.json):
- localization_accuracy — % of tasks with non-empty candidate_files
- impact_prediction_accuracy — % of edit tasks where impact_result had affected_files
- context_compression_ratio — chars_in/chars_out when context_compressor activates
- long_horizon_success_rate — % of repository tasks completed successfully

Report output: `reports/repository_eval_report.json`; `--merge` merges into `reports/eval_report.json`

**Phase 10.5 graph-guided localization metrics** (run_localization_eval.py, dataset: tests/localization_tasks.json):
- file_localization_accuracy — % correct file in top-k (target ≥ 85%)
- function_localization_accuracy — % correct symbol in top-k (target ≥ 75%)
- top_k_recall — hits at k=1, 3, 5
- average_graph_traversal_depth — mean dependency traversal count
- average_candidate_files — mean files returned by localization
- retry_reduction — target ≥ 30% reduction vs baseline (requires baseline comparison)

Report output: `reports/localization_report.json`

**Phase 11 intelligence metrics** (run_autonomous_eval.py, run_multi_agent_eval.py with intelligence layer):
- solution_reuse_rate — % of tasks where similar_solutions was non-empty and influenced the plan
- experience_improvement — delta in task_success_rate when experience_hints present vs absent
- repeat_failure_rate — % of tasks that failed after a similar past task had succeeded
- developer_acceptance — % of solutions marked developer_accepted=true (when feedback collected)

**Phase 12 workflow metrics** (run_workflow_eval.py, dataset: tests/workflow_tasks.json):
- pr_success_rate — % of tasks with valid PR generated (title + files/description)
- ci_pass_rate — % of tasks where CI (pytest, ruff) passed
- developer_acceptance_rate — % of solutions accepted by developer (when feedback collected)
- avg_retries_per_task — mean retries before success or fail
- pr_merge_latency — mean time from issue to PR ready (seconds)
- issue_to_pr_success — % of tasks where goal_success and PR generated

Report output: `reports/workflow_eval_report.json`

**Phase 16 failure mining metrics** (run_failure_mining.py, dataset: tests/failure_mining_tasks.json):
- success_rate — % of tasks that achieved SUCCESS
- retry_success_rate — % of FAILURE→SUCCESS across retries
- avg_attempts — mean attempts per task
- retrieval_miss_rate — % of failures classified as retrieval_miss
- patch_error_rate — % of failures classified as incorrect_patch or syntax_error_patch
- localization_error_rate — % of failures classified as wrong_file_localization
- avg_steps_success — mean trajectory_length across success records
- avg_steps_failure — mean trajectory_length across failure records
- loop_failure_rate — % of failure records where failure_type = loop_failure

Report output: `reports/failure_analysis.md`, `reports/failure_stats.json`
