# AutoStudio Documentation

## Overview

| Document | Description |
|----------|--------------|
| [PROMPT_ARCHITECTURE.md](PROMPT_ARCHITECTURE.md) | Prompt layer: all prompts, pipeline position, design philosophy, safety risks, testing. |
| [CONFIGURATION.md](CONFIGURATION.md) | Centralized config: all modules (`config/`), env overrides, validation rules. |
| [AGENT_LOOP_WORKFLOW.md](AGENT_LOOP_WORKFLOW.md) | End-to-end agent flow: instruction → plan → execute → validate → replan. ToolGraph → Router → PolicyEngine; step dispatch; SEARCH/EDIT/INFRA/EXPLAIN paths; repo_map lookup + anchor detection → hybrid retrieval; run_retrieval_pipeline (anchor detection → symbol_expander + expand → read → build_context); context_builder_v2; context gate; model routing; diff planner; validator empty-context rule. |
| [AGENT_CONTROLLER.md](AGENT_CONTROLLER.md) | Full pipeline: run_controller, instruction router (default), safety limits, test repair, task memory, trace logging. |
| [ROUTING_ARCHITECTURE_REPORT.md](ROUTING_ARCHITECTURE_REPORT.md) | Routing architecture: instruction router, tool graph (retrieve_graph/vector/grep/list_dir), Serena and filesystem rules, query rewrite prompts, replanner. |
| [REPOSITORY_SYMBOL_GRAPH.md](REPOSITORY_SYMBOL_GRAPH.md) | Symbol graph design and implementation: indexing, graph storage, repo map (builder, lookup, incremental updater), anchor detection, change detector, vector search, retrieval cache, editing pipeline (diff_planner, patch_generator, ast_patcher, patch_validator, patch_executor), testing and validation. |
| [CODING_AGENT_ARCHITECTURE_GUIDE.md](CODING_AGENT_ARCHITECTURE_GUIDE.md) | Architecture patterns for coding agents: retrieval, code understanding, editing, fault tolerance, memory. |
| [../dev/roadmap/phase_1_pipeline.md](../dev/roadmap/phase_1_pipeline.md) | Phase 1 pipeline convergence: steps 1–8, verification tests, full system test (`python -m agent "Explain how StepExecutor works"`). |
| [../dev/roadmap/phase_3_scenarios.md](../dev/roadmap/phase_3_scenarios.md) | Phase 3 scenario evaluation: 40-task benchmark, `run_principal_engineer_suite --scenarios`, `reports/eval_report.json`. |
| [../dev/roadmap/phase_4_reliability.md](../dev/roadmap/phase_4_reliability.md) | Phase 4 reliability: failure policies, replanner safeguards, execution limits, retrieval fallback, patch safety, trace integrity, failure mining, stress testing. |
| [../dev/roadmap/phase_5_metrics.md](../dev/roadmap/phase_5_metrics.md) | Phase 5 capability expansion: dev_tasks.json, run_capability_eval, metrics dashboard (task_success_rate, retrieval_recall, planner_accuracy, edit_success_rate, avg_latency, avg_files_modified, avg_steps_per_task, avg_patch_size). |
| [../dev/roadmap/phase_6_developer_experience.md](../dev/roadmap/phase_6_developer_experience.md) | Phase 6 developer experience: autostudio CLI (explain, edit, trace, chat, debug), interactive session, slash-commands, session memory, live step visualization, UX metrics. |
| [../dev/roadmap/phase_7_reliability_hardening.md](../dev/roadmap/phase_7_reliability_hardening.md) | Phase 7 reliability hardening: per-step timeout, tool validation (validate_step_input), context guardrail; autonomous mode (agent/autonomous/, run_autonomous). |

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
- **Run agent:** `python -m agent "instruction"`
- **Run controller:** `from agent.orchestrator.agent_controller import run_controller`
- **Run autonomous (Mode 2, Phase 7):** `from agent.autonomous import run_autonomous` — `run_autonomous(goal, project_root)`; goal-driven loop; reuses dispatcher, retrieval, editing pipeline |
- **Index repo:** `python -m repo_index.index_repo <path>` — respects `.gitignore` by default; `-v` verbose, `--no-gitignore` to include all files; API supports `include_dirs`, `ignore_gitignore`, `verbose`
- **E2E tests:** `python -m pytest tests/test_agent_e2e.py -v` (default: try LLM, fallback to mock; `--mock` to force mock)
- **Agent loop tests:** `python -m pytest tests/test_agent_loop.py -v` (execution loop, planner→executor→results; mocks dispatch for fast runs)
- **Explain gate tests:** `python -m pytest tests/test_explain_gate.py -v` (context gate: ensure_context_before_explain)
- **Repo map tests:** `python -m pytest tests/test_repo_map.py -v` (repo_map build, lookup, anchor detection, incremental update)
- **Robustness tests:** `python -m pytest tests/test_agent_robustness.py -v` (failure scenarios, replan, fallback, no corruption)
- **Trajectory tests:** `python -m pytest tests/test_agent_trajectory.py -v --mock` (complex tasks: multi-search, conflict resolver, repair loop)
- **Observability tests:** `python -m pytest tests/test_observability.py -v` (trace creation, plan, tool calls, errors, patch results)
- **Planner eval:** `python -m planner.planner_eval`
- **Router eval:** `python -m router_eval.router_eval`
- **Phase 3 scenario eval:** `python scripts/run_principal_engineer_suite.py --scenarios` — 40 tasks via run_controller; output: `reports/eval_report.json`; dataset: `tests/agent_scenarios.json`
- **Phase 5 capability eval:** `python scripts/run_capability_eval.py` — 40 developer tasks via run_agent; output: `reports/eval_report.json`; dataset: `tests/dev_tasks.json`; `--mock` for CI
- **Phase 4 failure mining:** `python scripts/run_principal_engineer_suite.py --failure-mining --mining-reps 10` — aggregates failures to `dev/evaluation/failure_patterns.md`
- **Phase 4 stress test:** `python scripts/run_principal_engineer_suite.py --stress --stress-reps 5` — varied seeds; output: `reports/stress_report.json`
- **Trace replay:** `autostudio trace <task_id>` or `autostudio debug last-run` (interactive) — or `python scripts/replay_trace.py <trace_id>` — shows stages, events, execution_counts, step success/failure
- **Phase 6 developer workflow tests:** `python -m pytest tests/test_developer_workflow.py -v` — session memory, slash-commands, multi-turn scenarios
- **Agent eval (legacy):** `python scripts/evaluate_agent.py --plan-only` (light) or `python scripts/evaluate_agent.py` (full); dataset: `tests/agent_eval.json`; metrics: task_success_rate, retrieval_recall, planner_accuracy, latency
