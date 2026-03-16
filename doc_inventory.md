# AutoStudio Documentation Inventory

Generated as part of the documentation audit. Lists all documentation sources with path, purpose, last update indicators, and modules described.

## Root Documentation

| File Path | Document Purpose | Last Update Indicators | Modules Described |
|-----------|------------------|------------------------|-------------------|
| README.md | Master entry point: project overview, architecture diagram, quick start, project structure, core components, execution pipeline, configuration, tools, testing, subsystems, evaluation | Pipeline diagram predates Phases 17/18; no BM25, RRF, reranker, dedup | Plan resolver, agent loop, execution, search path, post-search pipeline, explain path, editing pipeline, workflow, failure mining |

## Docs/ Directory

| File Path | Document Purpose | Last Update Indicators | Modules Described |
|-----------|------------------|------------------------|-------------------|
| Docs/README.md | Documentation index with one-line descriptions; agent robustness; quick links for CLI and eval scripts | References RETRIEVAL_ARCHITECTURE with Phase 17/18 content | All Docs/ files, dev/roadmap phases |
| Docs/AGENT_LOOP_WORKFLOW.md | End-to-end step dispatch workflow; Mermaid diagrams for every path | Detailed; may need retrieval pipeline update | Instruction router, plan resolver, execution loop, SEARCH/EDIT/INFRA/EXPLAIN, policy engine, validation, replanner |
| Docs/AGENT_CONTROLLER.md | Full production pipeline: run_controller, modes, safety limits, trace events | Includes trace events, UX metrics | run_controller, run_deterministic, task memory, trace logging, EDIT flow |
| Docs/CODING_AGENT_ARCHITECTURE_GUIDE.md | Anti-patterns guide for AI coding agents | Architecture patterns, production practices | Model client, retrieval, code understanding, editing, fault tolerance, memory |
| Docs/CONFIGURATION.md | Centralized config reference; all modules, env overrides, validation | Has Phase 17 reranker/BM25/RRF; may lack Phase 18 graph config | agent_config, editing_config, retrieval_config, repo_graph_config, observability_config, etc. |
| Docs/FAILURE_MINING.md | Phase 16 failure pattern mining architecture and usage | Dedicated failure mining doc | agent/failure_mining/, trajectory_loader, failure_extractor, failure_clusterer, root_cause_report, failure_judge |
| Docs/PROMPT_ARCHITECTURE.md | Full prompt layer: PromptRegistry, versioning, all prompts, pipeline position | Phase 13/14 content | PromptRegistry, context control, token budgeting, guardrails, A/B testing |
| Docs/prompt_engineering_rules.md | Phase 13 governance rules for prompts | 7 rules | PromptRegistry API, FailureRecord API, PromptTemplate |
| Docs/REPOSITORY_SYMBOL_GRAPH.md | Symbol graph design, implementation, retrieval flow | 9-step retrieval flow; may need Phase 18 updates | repo_index/, repo_graph/, agent/retrieval/, editing pipeline |
| Docs/RETRIEVAL_ARCHITECTURE.md | Full retrieval pipeline: BM25, vector, graph, RRF, reranker, dedup, telemetry | Most current retrieval doc; Phase 17/18 | BM25, RRF, graph expansion, reference lookup, call-chain context, dedup, cross-encoder reranker, config, telemetry |
| Docs/ROUTING_ARCHITECTURE_REPORT.md | Routing architecture post-refactor | Router entrypoints, tool graph | Instruction router, tool graph, categories, replanner |
| Docs/WORKFLOW.md | Phase 12 developer workflow layer | issue → PR → CI → review | agent/workflow/, CLI, flow, safety limits |
| Docs/phase.md | 5-phase architectural refactor plan | Target architecture | Instruction router, category unification, tool graph alignment |
| Docs/repo_pattern_anti_pattterns.md | Architecture guardrails | Design goals, constraints, patterns, anti-patterns | Core principles, patterns, anti-patterns |

## Component-Level READMEs

| File Path | Document Purpose | Last Update Indicators | Modules Described |
|-----------|------------------|------------------------|-------------------|
| agent/prompts/README.md | Phase 13 prompt migration compatibility | Versioned prompts vs legacy YAML | get_prompt(), PromptRegistry, registry name table |
| planner/README.md | Planner module documentation | Architecture, step format, evaluation | planner.py, planner_eval, step format |
| router_eval/README.md | Router evaluation harness | 7 router phases, run commands | router_eval, routers, config |

## Dev Roadmap (Phase Files)

| File Path | Purpose | Status |
|-----------|---------|--------|
| dev/roadmap/phase_1_pipeline.md | Pipeline convergence | Completed |
| dev/roadmap/phase_2_integration.md | Component integration | Completed |
| dev/roadmap/phase_3_scenarios.md | Scenario testing | Completed |
| dev/roadmap/phase_4_reliability.md | Failure analysis | Completed |
| dev/roadmap/phase_5_metrics.md | Metrics dashboard | Completed |
| dev/roadmap/phase_6_developer_experience.md | CLI, chat, slash-commands | Completed |
| dev/roadmap/phase_7_reliability_hardening.md | Per-step timeout, autonomous | Completed |
| dev/roadmap/phase_8_autonomous_mode.md | agent/meta/, retry loop | Completed |
| dev/roadmap/phase_9_workflow_integration.md | agent/roles/, multi-agent | Completed |
| dev/roadmap/phase_10_capability_expansion.md | agent/repo_intelligence/ | In Progress |
| dev/roadmap/phase_10-5_graph_traversal.md | agent/retrieval/localization/ | In Progress |
| dev/roadmap/phase_11_intelligence.md | agent/intelligence/ | In Progress |
| dev/roadmap/phase_12_last_stop.md | agent/workflow/ | In Progress |
| dev/roadmap/phase_13_prompt_framwork.md | agent/prompt_system/ | Completed |
| dev/roadmap/phase_14_prompt_layer_2.md | Token budgeting | In Progress |
| dev/roadmap/phase_15_trajectory.md | TrajectoryLoop, retry diversity | Completed |
| dev/roadmap/phase_16_failure_mining.md | agent/failure_mining/ | In Progress |
| dev/roadmap/phase_17_retrieval_reranker.md | Cross-encoder reranker | Completed |
| dev/roadmap/phase_18_retrieval_precision_upgrade.md | Graph expansion, reference lookup, dedup | Completed |
| dev/roadmap/failure-pattern-mining.md | Phase 16 methodology | Reference |

## Missing Documentation (Gaps)

| Path | Status |
|------|--------|
| Docs/ARCHITECTURE.md | Does not exist |
| Docs/OBSERVABILITY.md | Does not exist |
| agent/retrieval/README.md | Does not exist |
| agent/retrieval/reranker/README.md | Does not exist |
| agent/meta/README.md | Does not exist |
| agent/failure_mining/README.md | Does not exist |
