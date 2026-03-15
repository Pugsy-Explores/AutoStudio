# AutoStudio Documentation

## Overview

| Document | Description |
|----------|--------------|
| [PROMPT_ARCHITECTURE.md](PROMPT_ARCHITECTURE.md) | Prompt layer: PromptRegistry (Phase 13), versioning, all prompts, pipeline position, design philosophy, safety risks, testing. |
| [prompt_engineering_rules.md](prompt_engineering_rules.md) | Phase 13 governance: 1 prompt = 1 capability, versioning, evaluation, failure logging, Rules 6–7 (eval coverage, context budget), guardrails at LLM boundary, A/B testing. |
| [CONFIGURATION.md](CONFIGURATION.md) | Centralized config: all modules (`config/`), env overrides, validation rules. |
| [AGENT_LOOP_WORKFLOW.md](AGENT_LOOP_WORKFLOW.md) | End-to-end agent flow: instruction → plan → execute → validate → replan. ToolGraph → Router → PolicyEngine; step dispatch; SEARCH/EDIT/INFRA/EXPLAIN paths; repo_map lookup + anchor detection → hybrid retrieval; run_retrieval_pipeline (anchor detection → localization [Phase 10.5] → symbol_expander + expand → read → build_context); context_builder_v2; context gate; model routing; diff planner; validator empty-context rule. |
| [AGENT_CONTROLLER.md](AGENT_CONTROLLER.md) | Full pipeline: run_controller (mode: deterministic/autonomous/multi_agent), run_deterministic, all tools via dispatch, safety limits, task memory, trace logging. |
| [ROUTING_ARCHITECTURE_REPORT.md](ROUTING_ARCHITECTURE_REPORT.md) | Routing architecture: instruction router, tool graph (retrieve_graph/vector/grep/list_dir), Serena and filesystem rules, query rewrite prompts, replanner. |
| [REPOSITORY_SYMBOL_GRAPH.md](REPOSITORY_SYMBOL_GRAPH.md) | Symbol graph design and implementation: indexing, graph storage, repo map (builder, lookup, incremental updater), anchor detection, change detector, vector search, retrieval cache, editing pipeline (diff_planner, patch_generator, ast_patcher, patch_validator, patch_executor), testing and validation. |
| [CODING_AGENT_ARCHITECTURE_GUIDE.md](CODING_AGENT_ARCHITECTURE_GUIDE.md) | Architecture patterns for coding agents: retrieval, code understanding, editing, fault tolerance, memory. |
| [FAILURE_MINING.md](FAILURE_MINING.md) | Phase 16 failure pattern mining: trajectory_loader, failure_extractor, failure_clusterer, root_cause_report; loop/hallucination detection; run_failure_mining.py; CI guardrails. |
| [../dev/roadmap/phase_1_pipeline.md](../dev/roadmap/phase_1_pipeline.md) | Phase 1 pipeline convergence: steps 1–8, verification tests, full system test (`python -m agent "Explain how StepExecutor works"`). |
| [../dev/roadmap/phase_3_scenarios.md](../dev/roadmap/phase_3_scenarios.md) | Phase 3 scenario evaluation: 40-task benchmark, `run_principal_engineer_suite --scenarios`, `reports/eval_report.json`. |
| [../dev/roadmap/phase_4_reliability.md](../dev/roadmap/phase_4_reliability.md) | Phase 4 reliability: failure policies, replanner safeguards, execution limits, retrieval fallback, patch safety, trace integrity, failure mining, stress testing. |
| [../dev/roadmap/phase_5_metrics.md](../dev/roadmap/phase_5_metrics.md) | Phase 5 capability expansion: dev_tasks.json, run_capability_eval, metrics dashboard (task_success_rate, retrieval_recall, planner_accuracy, edit_success_rate, avg_latency, avg_files_modified, avg_steps_per_task, avg_patch_size). |
| [../dev/roadmap/phase_6_developer_experience.md](../dev/roadmap/phase_6_developer_experience.md) | Phase 6 developer experience: autostudio CLI (explain, edit, trace, chat, debug), interactive session, slash-commands, session memory, live step visualization, UX metrics. |
| [../dev/roadmap/phase_7_reliability_hardening.md](../dev/roadmap/phase_7_reliability_hardening.md) | Phase 7 reliability hardening: per-step timeout, tool validation (validate_step_input), context guardrail; autonomous mode (agent/autonomous/, run_autonomous). |
| [../dev/roadmap/phase_8_autonomous_mode.md](../dev/roadmap/phase_8_autonomous_mode.md) | Phase 8 self-improving loop: agent/meta/ (evaluator, critic, retry_planner, trajectory_store); outer retry loop; reflection metrics. |
| [../dev/roadmap/phase_15_trajectory.md](../dev/roadmap/phase_15_trajectory.md) | Phase 15 trajectory improvement loop: TrajectoryLoop, attempt/start_time/end_time in trajectory_store, retry-strategy fallback, retry diversity (DIVERSITY_SEQUENCE), failure_type telemetry, config; run_retry_eval. |
| [../dev/roadmap/phase_16_failure_mining.md](../dev/roadmap/phase_16_failure_mining.md) | Phase 16 failure pattern mining: agent/failure_mining/ (trajectory_loader, failure_extractor, failure_clusterer, root_cause_report); loop/hallucination detection; reports/failure_analysis.md; run_failure_mining.py. |
| [../dev/roadmap/phase_9_workflow_integration.md](../dev/roadmap/phase_9_workflow_integration.md) | Phase 9 hierarchical multi-agent: agent/roles/ (supervisor, planner, localization, edit, test, critic); run_multi_agent(); AgentWorkspace; safety limits; multi_agent_tasks.json; run_multi_agent_eval. |
| [../dev/roadmap/phase_10_capability_expansion.md](../dev/roadmap/phase_10_capability_expansion.md) | Phase 10 repository-scale intelligence: agent/repo_intelligence/ (repo_summary_graph, architecture_map, impact_analyzer, context_compressor, long_horizon_planner); repository_tasks.json; run_repository_eval. |
| [../dev/roadmap/phase_10-5_graph_traversal.md](../dev/roadmap/phase_10-5_graph_traversal.md) | Phase 10.5 graph-guided localization: agent/retrieval/localization/ (dependency_traversal, execution_path_analyzer, symbol_ranker, localization_engine); localization_tasks.json; run_localization_eval. |
| [../dev/roadmap/phase_11_intelligence.md](../dev/roadmap/phase_11_intelligence.md) | Phase 11 intelligence layer: agent/intelligence/ (solution_memory, task_embeddings, experience_retriever, developer_model, repo_learning); experience_hints; solution storage on success. |
| [../dev/roadmap/phase_12_last_stop.md](../dev/roadmap/phase_12_last_stop.md) | Phase 12 developer workflow: agent/workflow/ (issue_parser, pr_generator, ci_runner, code_review_agent, developer_feedback, workflow_controller); CLI: issue, fix, pr, review, ci; workflow_tasks.json; run_workflow_eval. |
| [../dev/roadmap/phase_13_prompt_framwork.md](../dev/roadmap/phase_13_prompt_framwork.md) | Phase 13 prompt infrastructure: agent/prompt_system/ (registry, versioning, guardrails, skills, context, retry_strategies, observability); agent/prompt_eval/; agent/prompt_versions/; scripts/run_prompt_ci.py. |
| [WORKFLOW.md](WORKFLOW.md) | Phase 12 workflow layer: modules, CLI, flow, safety limits, trace events, persistence, evaluation. |

## Agent robustness

The agent handles failure scenarios without crashing or corrupting the repository:

- **Nonexistent symbol search:** Policy retries with rewritten query; hybrid retrieval (parallel graph + vector + grep) or fallback chain exhausted before returning failure.
- **Invalid edit / patch validator failure:** Rollback restores all modified files; no partial writes.
- **Graph lookup empty:** Falls through to vector search, then Serena.
- **Replan on failure:** LLM-based replanner receives failed_step and error; agent_loop: up to 3 replans, 2 step retries before replan; agent_controller: up to 5 replans; failed steps do not advance; remaining steps retried.
- **Retrieval fallback:** When graph/vector/grep return empty, file_search fallback guarantees ≥1 snippet (Phase 4).
- **Result classification:** SUCCESS, RETRYABLE_FAILURE, FATAL_FAILURE; FATAL stops without replan (Phase 4).
- **Pre-dispatch validation (Phase 7):** `validate_step_input` checks action, description; raises InvalidStepError; avoids invalid tool calls.
- **Per-step timeout (Phase 7):** Single slow tool cannot consume full task budget; step timeout returns FATAL_FAILURE.
- **Context guardrail (Phase 7):** MAX_CONTEXT_CHARS truncates context before LLM; logs context_guardrail_triggered.

See `tests/test_agent_robustness.py` for coverage.

## Quick links

- **CLI (Phase 6):** `autostudio explain <symbol>`, `autostudio edit <instruction>`, `autostudio chat`, `autostudio trace <task_id>`, `autostudio debug last-run` — or `python -m agent.cli.entrypoint <subcommand>`
- **CLI (Phase 12 workflow):** `autostudio issue <text>`, `autostudio fix <instruction>`, `autostudio pr`, `autostudio review`, `autostudio ci`
- **Run agent:** `python -m agent "instruction"`
- **Run controller:** `from agent.orchestrator.agent_controller import run_controller` — `run_controller(instruction, project_root, mode="deterministic")`; modes: deterministic (default), autonomous, multi_agent
- **Run autonomous (Mode 2, Phase 7/8/11/15):** `from agent.autonomous import run_autonomous` — `run_autonomous(goal, project_root, max_retries=MAX_RETRY_ATTEMPTS)`; goal-driven loop; Phase 11: experience_retriever injects experience_hints before planning; when max_retries>1, TrajectoryLoop (Phase 15) runs meta loop (attempt→evaluate→critic→retry); on success, stores solution to intelligence layer; reuses dispatcher, retrieval, editing pipeline
- **Run multi-agent (Phase 9):** `from agent.roles import run_multi_agent` — `run_multi_agent(goal, project_root, success_criteria=...)`; supervisor → planner → localization → edit → test → critic (on failure); reuses dispatcher, retrieval, editing pipeline; limits: max_agent_steps=30, max_patch_attempts=3, max_runtime=120s. **Phase 10 repo intelligence:** Before planner, builds repo_summary and architecture_map; plan_long_horizon when architecture present; impact_analyzer after edit; context_compressor in retrieval when repo_summary present.
- **Index repo:** `python -m repo_index.index_repo <path>` — respects `.gitignore` by default; `-v` verbose, `--no-gitignore` to include all files; API supports `include_dirs`, `ignore_gitignore`, `verbose`
- **E2E tests:** `python -m pytest tests/test_agent_e2e.py -v` (default: try LLM, fallback to mock; `--mock` to force mock)
- **Agent loop tests:** `python -m pytest tests/test_agent_loop.py -v` (execution loop, planner→executor→results; mocks dispatch for fast runs)
- **Explain gate tests:** `python -m pytest tests/test_explain_gate.py -v` (context gate: ensure_context_before_explain)
- **Repo map tests:** `python -m pytest tests/test_repo_map.py -v` (repo_map build, lookup, anchor detection, incremental update)
- **Robustness tests:** `python -m pytest tests/test_agent_robustness.py -v` (failure scenarios, replan, fallback, no corruption)
- **Trajectory tests:** `python -m pytest tests/test_agent_trajectory.py -v --mock` (complex tasks: multi-search, conflict resolver, repair loop)
- **Observability tests:** `python -m pytest tests/test_observability.py -v` (trace creation, plan, tool calls, errors, patch results)
- **Trajectory loop tests:** `python -m pytest tests/test_trajectory_loop.py tests/test_autonomous_meta.py -v` (Phase 15: success/retry/max_retry, trajectory storage, retry-strategy fallback)
- **Failure mining tests:** `python -m pytest tests/test_failure_mining.py -v` (Phase 16: trajectory parsing, loop/hallucination detection, clustering, report generation)
- **Planner eval:** `python -m planner.planner_eval`
- **Router eval:** `python -m router_eval.router_eval`
- **Phase 3 scenario eval:** `python scripts/run_principal_engineer_suite.py --scenarios` — 40 tasks via run_controller; output: `reports/eval_report.json`; dataset: `tests/agent_scenarios.json`
- **Phase 5 capability eval:** `python scripts/run_capability_eval.py` — 40 developer tasks via run_agent; output: `reports/eval_report.json`; dataset: `tests/dev_tasks.json`; `--mock` for CI
- **Phase 8 autonomous eval:** `python scripts/run_autonomous_eval.py` — 7 autonomous tasks via run_autonomous; output: `reports/autonomous_eval_report.json`; reflection metrics: attempts_per_goal, retry_success_rate, critic_accuracy, trajectory_reuse; `--mock` for CI
- **Phase 15 retry eval:** `python scripts/run_retry_eval.py` — autonomous tasks with retries; output: `reports/retry_eval_report.json`; metrics: success_rate, retry_success_rate, attempts_per_task; `--mock` for CI; `--limit N` for quick runs
- **Phase 9 multi-agent eval:** `python scripts/run_multi_agent_eval.py` — 30 multi-agent tasks via run_multi_agent; output: `reports/multi_agent_eval_report.json`; metrics: goal_success_rate, agent_delegations, critic_accuracy, localization_accuracy, patch_success_rate; `--mock` for CI; `--merge` to merge into eval_report.json
- **Phase 10 repository eval:** `python scripts/run_repository_eval.py` — 40 repository tasks via run_multi_agent (with repo intelligence); output: `reports/repository_eval_report.json`; metrics: localization_accuracy, impact_prediction_accuracy, context_compression_ratio, long_horizon_success_rate; `--mock` for CI; `--merge` to merge into eval_report.json
- **Phase 10.5 localization eval:** `python scripts/run_localization_eval.py` — 10 localization tasks; output: `reports/localization_report.json`; metrics: file_accuracy, function_accuracy, top_k_recall, avg_graph_nodes; `--mock` for CI; `--limit N` for quick validation
- **Phase 11 intelligence:** `agent/intelligence/` — experience_retriever injects experience_hints before planning; solution_memory, task_embeddings, developer_model, repo_learning store and learn from successful runs; metrics: solution_reuse_rate, experience_improvement, repeat_failure_rate, developer_acceptance
- **Phase 12 workflow eval:** `python scripts/run_workflow_eval.py` — 8 workflow tasks via run_workflow; output: `reports/workflow_eval_report.json`; metrics: pr_success_rate, ci_pass_rate, issue_to_pr_success; `--mock` for CI; `--limit N` for quick validation
- **Phase 13 prompt CI:** `python scripts/run_prompt_ci.py` — run prompt eval against `tests/prompt_eval_dataset.json` (100 cases: navigation, planning, editing, refactoring, test-fixing, repo-reasoning); compare with `dev/prompt_eval_results/baseline.json`; exit(1) on regression (task_success, json_validity, tool_misuse); `--save-baseline` to set baseline; `--prompt NAME` for specific prompt; A/B test via `agent.prompt_system.versioning.run_ab_test()`
- **Phase 16 failure mining:** `python scripts/run_failure_mining.py --tasks 300` — run 300 tasks, load trajectories, extract failures (loop/hallucination detection), cluster, generate `reports/failure_analysis.md` and `reports/failure_stats.json`; `--skip-run` to analyze existing trajectories only; `--use-judge` for LLM relabel of unknown types; CI guardrails: retrieval_miss_rate < 40%, patch_error_rate < 25%
- **Phase 4 failure mining:** `python scripts/run_principal_engineer_suite.py --failure-mining --mining-reps 10` — aggregates failures to `dev/evaluation/failure_patterns.md`
- **Phase 4 stress test:** `python scripts/run_principal_engineer_suite.py --stress --stress-reps 5` — varied seeds; output: `reports/stress_report.json`
- **Trace replay:** `autostudio trace <task_id>` or `autostudio debug last-run` (interactive) — or `python scripts/replay_trace.py <trace_id>` — shows stages, events, execution_counts, step success/failure
- **Phase 6 developer workflow tests:** `python -m pytest tests/test_developer_workflow.py -v` — session memory, slash-commands, multi-turn scenarios
- **Agent eval (legacy):** `python scripts/evaluate_agent.py --plan-only` (light) or `python scripts/evaluate_agent.py` (full); dataset: `tests/agent_eval.json`; metrics: task_success_rate, retrieval_recall, planner_accuracy, latency
