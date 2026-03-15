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
