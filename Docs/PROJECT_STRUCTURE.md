# AutoStudio — Project Structure

**AutoStudio** is a repository-aware autonomous coding agent that converts natural-language instructions into structured execution plans. It uses LLM-powered planning, hybrid retrieval (graph + vector + lexical + grep), deterministic tool dispatch, and structured patch generation.

**GitHub:** `git@github.com:Pugsy-Explores/AutoCodeStudio.git`

---

## Top-Level Files

| File | Purpose |
|------|---------|
| `pyproject.toml` | Package config; defines `autostudio` CLI entrypoint |
| `requirements.txt` / `requirements-dev.txt` | Runtime and dev dependencies |
| `mcp.json` | MCP server config (Serena code search) |
| `index_repo.py` | Legacy embedding indexer (superseded by `repo_index/`) |
| `mcp_retriever.py` | Legacy ChromaDB retrieval API |
| `system_components.md` | Component inventory |
| `README.md` | Comprehensive documentation (architecture, config, usage) |

---

## Core Directories

### `agent/` — Main Agent Package (the heart of the system)

The largest module, containing all runtime orchestration:

| Subdirectory | Role |
|-------------|------|
| `orchestrator/` | **Entry points**: `agent_controller.py` (run_controller), `deterministic_runner.py` (run_hierarchical → ReAct execution_loop), `plan_resolver.py`, `replanner.py`, `goal_evaluator.py`, `validator.py` — ReAct primary; plan-based path via REACT_MODE=0 |
| `execution/` | **Step dispatch**: `StepExecutor`, `step_dispatcher.py` (routes SEARCH/EDIT/INFRA/EXPLAIN), `tool_graph.py`, `policy_engine.py`, `explain_gate.py`, `mutation_strategies.py` |
| `retrieval/` | **Hybrid retrieval pipeline**: `search_pipeline.py` (BM25 + graph + vector + grep in parallel), `retrieval_pipeline.py` (anchor → expand → rerank → prune), `query_rewriter.py`, `context_builder.py`, `context_ranker.py`, `context_pruner.py`, `reranker/`, `localization/` |
| `tools/` | **Tool adapters**: filesystem, terminal, Serena MCP, reference lookup, context7 |
| `routing/` | **Instruction router**: classifies intent (CODE_EDIT, CODE_SEARCH, EXPLAIN, INFRA) before planner |
| `memory/` | **State management**: `AgentState`, `StepResult`, task memory, session memory, task index |
| `meta/` | **Reflection layer (Phase 5)**: trajectory memory, evaluator, critic, retry planner |
| `models/` | **LLM client**: model router (SMALL/REASONING/REASONING_V2), config, guardrails |
| `autonomous/` | **Mode 2 (future)**: goal-driven loop with state observer and action selector |
| `roles/` | **Phase 9 multi-agent**: supervisor, planner, localization, edit, test, critic agents |
| `runtime/` | **Edit→test→fix loop**: execution_loop (snapshot rollback), syntax validator, retry guard |
| `intelligence/` | **Phase 11**: solution memory, task embeddings, experience retrieval, developer model |
| `repo_intelligence/` | **Phase 10**: repo summary graph, architecture map, impact analyzer, context compressor |
| `workflow/` | **Phase 12**: issue parser → PR generator → CI runner → code review → feedback |
| `prompt_system/` | **Phase 13**: PromptRegistry, versioning, guardrails, skills, context budgeting |
| `prompt_eval/` | Prompt benchmarking and failure analysis |
| `prompts/` | Legacy YAML prompts (compatibility shim) |
| `prompt_versions/` | Versioned prompts per component (planner/v1.yaml, etc.) |
| `observability/` | Trace logging, UX metrics |
| `cli/` | CLI entrypoints: `autostudio explain/edit/chat/trace/debug/issue/fix/pr/review/ci` |
| `failure_mining/` | Phase 16: trajectory failure extraction, clustering, root cause reports |
| `eval/` | Agent evaluation harnesses |
| `contracts/` | Interface contracts |
| `strategy/` | Planning strategies |

### `config/` — Centralized Configuration

All runtime configuration in one place:

| File | Purpose |
|------|---------|
| `agent_config.py` | Agent loop limits: runtime, replan, step timeout, context chars |
| `agent_runtime.py` | Edit/test/fix loop: MAX_EDIT_ATTEMPTS, patch limits, ENABLE_SANDBOX |
| `editing_config.py` | Patch and file limits |
| `retrieval_config.py` | Retrieval budgets, hybrid flags, cache size |
| `router_config.py` | Instruction router (ROUTER_TYPE, ENABLE_INSTRUCTION_ROUTER) |
| `tool_graph_config.py` | Tool graph enable/disable |
| `repo_graph_config.py` | Symbol graph paths (.symbol_graph/) |
| `repo_intelligence_config.py` | Phase 10: repo scan, architecture, impact, context limits |
| `observability_config.py` | Trace settings |
| `logging_config.py` | Log level/format |
| `policy_config.py` / `policy_config.yaml` | Policy engine configuration |
| `context_limits.py` | Context window limits |
| `tool_budgets.py` | Tool budget constraints |
| `config_validator.py` | Startup validation |
| `startup.py` | Startup checks |

### `editing/` — Editing Pipeline

Structured patch generation and execution:

| File | Purpose |
|------|---------|
| `diff_planner.py` | Plans diffs for EDIT steps |
| `conflict_resolver.py` | Detects/resolves edit conflicts (same symbol, same file, semantic overlap) |
| `patch_generator.py` | Converts plan to structured patches |
| `patch_validator.py` | Ensures code compiles and AST reparse succeeds |
| `patch_executor.py` | Applies validated patches (max 5 files, 200 lines); rollback on failure |
| `ast_patcher.py` | Tree-sitter AST-level patching (symbol-level and statement-level edits) |
| `semantic_diff.py` | AST-aware overlap detection |
| `merge_strategies.py` | Sequential and three-way merge strategies |
| `grounded_patch_generator.py` | Grounded patch generation |
| `patch_effectiveness.py` | Patch effectiveness tracking |
| `test_repair_loop.py` | Run tests after patch, repair on failure, flaky detection |
| `test_runner_utils.py` | Test runner utilities |

### `planner/` — Instruction → Plan

Converts natural language instructions into JSON plans with structured steps (`{id, action, description, reason}`).

| File | Purpose |
|------|---------|
| `planner.py` | `plan(instruction)` → `{steps: [{id, action, description, reason}]}` |
| `planner_prompts.py` | System prompts for the planner |
| `planner_utils.py` | Utility functions |
| `planner_dataset.json` | Evaluation dataset |
| `planner_eval.py` | Planner evaluation harness |

### `repo_index/` — Repository Indexing

Tree-sitter-based parser that scans repos, extracts symbols and dependency edges:

| File | Purpose |
|------|---------|
| `index_repo.py` | CLI: index_repo (--verbose, --no-gitignore) |
| `indexer.py` | scan_repo, index_repo (parallel, .gitignore, optional embeddings) |
| `parser.py` | parse_file (Tree-sitter) |
| `symbol_extractor.py` | extract_symbols from parsed AST |
| `dependency_extractor.py` | extract_edges (imports, calls, references) |

Creates `.symbol_graph/index.sqlite`, `symbols.json`, `repo_map.json`, and optionally `.symbol_graph/embeddings/`.

### `repo_graph/` — Symbol Graph

SQLite-backed symbol graph with query and update capabilities:

| File | Purpose |
|------|---------|
| `graph_storage.py` | SQLite nodes/edges storage |
| `graph_builder.py` | build_graph from extracted symbols/edges |
| `graph_query.py` | find_symbol, expand_neighbors (2-hop) |
| `repo_map_builder.py` | build_repo_map (`{modules, symbols, calls}`) |
| `repo_map_updater.py` | Incremental update after file edits |
| `change_detector.py` | Semantic change impact detection (risk levels: LOW/MEDIUM/HIGH) |

### `router_eval/` — Router Evaluation

Evaluation harness for instruction routing:

| File/Dir | Purpose |
|----------|---------|
| `router_eval.py` / `router_eval_v2.py` | Main evaluation scripts |
| `run_all_routers.py` | Run with production router |
| `dataset.py` / `dataset_v2.py` | Dataset loaders |
| `golden_dataset_v2.json` | Golden evaluation dataset |
| `adversarial_dataset_v2.json` | Adversarial test cases |
| `routers/` | Router implementations (baseline, fewshot, ensemble, final) |
| `prompts/` | Router prompts |
| `tests/` | Router-specific tests |

### `models/` — Reranker Models

Contains the cross-encoder reranker model files (Qwen3-Reranker-0.6B ONNX).

---

## Supporting Directories

### `tests/` — Test Suite (~120+ test files)

Comprehensive test coverage including:
- **Unit tests**: Individual component tests (retrieval, editing, routing, policy, etc.)
- **Integration tests**: `tests/integration/` — cross-component tests
- **E2E tests**: `test_agent_e2e.py` — full pipeline with mocked LLM
- **Trajectory tests**: `test_agent_trajectory.py` — complex multi-step agent runs
- **Robustness tests**: `test_agent_robustness.py` — failure scenarios, replan, fallback
- **Benchmark datasets**: JSON files with 40-300 tasks per phase
  - `agent_scenarios.json` — 40 scenarios (8 groups)
  - `dev_tasks.json` — 40 developer tasks (Phase 5)
  - `autonomous_tasks.json` — 7 autonomous tasks (Phase 8)
  - `multi_agent_tasks.json` — 30 multi-agent tasks (Phase 9)
  - `repository_tasks.json` — 40 repository tasks (Phase 10)
  - `localization_tasks.json` — 10 localization tasks (Phase 10.5)
  - `workflow_tasks.json` — 8 workflow tasks (Phase 12)
  - `failure_mining_tasks.json` — 300 failure mining tasks (Phase 16)

### `scripts/` — Evaluation and Utilities

| Script | Purpose |
|--------|---------|
| `run_principal_engineer_suite.py` | Phase 3/4: scenarios, failure mining, stress |
| `run_capability_eval.py` | Phase 5: dev_tasks.json evaluation |
| `run_autonomous_eval.py` | Phase 8: autonomous_tasks.json |
| `run_multi_agent_eval.py` | Phase 9: multi_agent_tasks.json |
| `run_repository_eval.py` | Phase 10: repository_tasks.json |
| `run_localization_eval.py` | Phase 10.5: localization_tasks.json |
| `run_workflow_eval.py` | Phase 12: workflow_tasks.json |
| `run_prompt_ci.py` | Phase 13: prompt CI (eval + regression) |
| `run_failure_mining.py` | Phase 16: trajectory failure analysis |
| `retrieval_daemon.py` | Unified retrieval daemon (reranker + embedding) |
| `reranker_daemon.py` | Standalone reranker daemon |
| `evaluate_agent.py` | Legacy agent evaluation |
| `replay_trace.py` | Trace replay |
| `report_bug.py` | Bug reporting |
| `validate_retrieval_pipeline.py` | Retrieval pipeline validation |
| `run_search_quality_audit.py` | Search quality auditing |

### `Docs/` — Documentation (~60+ files)

| Category | Examples |
|----------|---------|
| **Architecture** | `ARCHITECTURE.md`, `CODING_AGENT_ARCHITECTURE_GUIDE.md`, `RETRIEVAL_ARCHITECTURE.md` |
| **Workflows** | `AGENT_LOOP_WORKFLOW.md`, `AGENT_CONTROLLER.md`, `WORKFLOW.md` |
| **Configuration** | `CONFIGURATION.md` |
| **Prompts** | `PROMPT_ARCHITECTURE.md`, `prompt_engineering_rules.md`, `ALL_PROMPTS.md` |
| **Stage Reports** | `STAGE2_TAG_AUDIT_REPORT.md` through `STAGE42_BOUNDED_QUERY_VARIANTS_CLOSEOUT.md` |
| **RCA/Audits** | Various root cause analyses, heuristic audits, contamination audits |
| **Design** | Decision memos, implementation plans, closeout reports |

### `dev/` — Development Workflow

| Subdirectory | Purpose |
|-------------|---------|
| `bugs/` | Bug tracking: `backlog/`, `in_progress/`, `resolved/`, `bug_index.md`, templates |
| `roadmap/` | Phase 1–16 roadmap plans (`phase_1_pipeline.md` through `phase_16_failure_mining.md`) |
| `tasks/` | Task tracking: `backlog.md`, `in_progress.md`, `completed.md` |
| `evaluation/` | Failure patterns, metrics definitions, test tasks |
| `experiments/` | Experiment notes (fine-tuning, editing pipeline, retrieval tuning) |
| `prompt_eval_results/` | Prompt evaluation results |

### `infra/` — Infrastructure Scripts

| Script | Purpose |
|--------|---------|
| `setup_litellm.sh` | LiteLLM proxy setup |
| `setup_llm-cpp.sh` | llama.cpp setup |
| `download_models.sh` | Model download script |

### `reports/` — Evaluation Reports

Evaluation output files: `eval_report.json`, `failure_analysis.md`, `failure_stats.json`, `localization_report.json`, `ux_metrics.json`, `autonomous_eval_report.json`, `multi_agent_eval_report.json`, `repository_eval_report.json`, `workflow_eval_report.json`.

### `artifacts/` — Test Artifacts

Debug workspaces, A/B test results, Chroma fix artifacts, search quality audit reports.

---

## Architecture Summary (Execution Flow)

```
User Instruction
  → run_controller (agent/orchestrator/agent_controller.py)
    → run_attempt_loop (up to 3 attempts)
      → get_plan (instruction router → planner)
        → Step Loop (StepExecutor → Dispatcher → PolicyEngine)
          → SEARCH: hybrid retrieval (BM25 + graph + vector + grep → RRF → rerank → prune)
          → EDIT: diff_planner → conflict_resolver → patch_generator → execute_patch → test_repair
          → EXPLAIN: context gate → context_builder_v2 → LLM reasoning
          → INFRA: terminal command execution
        → GoalEvaluator (attempt-level success check)
        → On failure: Critic → RetryPlanner → next attempt
  → save_task (persist to .agent_memory/)
```

### SEARCH Pipeline Detail

```
SEARCH
  → repo_map_lookup + anchor_detection
  → hybrid_retrieve (parallel: BM25 + graph + vector + grep)
  → reciprocal_rank_fusion → top 20 candidates
  → anchor_detector.detect_anchors
  → localization_engine (graph-guided, Phase 10.5)
  → symbol_expander (2-hop graph expansion)
  → reference_lookup + call_chain_context
  → deduplicate → cross-encoder reranker (Qwen3-Reranker-0.6B)
  → context_ranker → context_pruner (max 6 snippets, 8000 chars)
```

### EDIT Pipeline Detail

```
EDIT
  → diff_planner.plan_diff (identify affected symbols + callers)
  → conflict_resolver.resolve_conflicts
  → patch_generator.to_structured_patches
  → run_edit_test_fix_loop:
      → snapshot files → execute_patch → validate_project (syntax)
      → run_tests → on failure: rollback, retry with critic feedback
  → update_index + update_repo_map (incremental)
```

---

## Key Design Principles

1. **Deterministic Pipeline (Mode 1)** — Default execution mode; workflow controlled by code, not LLM
2. **All tools via dispatcher** — No direct tool calls; dispatcher manages policy, retries, logging
3. **All LLM calls via model router** — No direct `openai.chat()` calls in business logic
4. **All state in AgentState** — Single source of truth; no hidden globals or caches
5. **Retrieval before reasoning** — Every LLM reasoning call must be preceded by retrieval
6. **Every decision traceable** — Full trace logging of decisions, tools, inputs, outputs
7. **Extend, don't replace** — Improvements extend existing modules; no parallel systems
8. **Safety layers mandatory** — All actions pass through dispatcher → policy_engine → validation

---

## Operational Modes

| Mode | Status | Entry Point | Description |
|------|--------|-------------|-------------|
| **Mode 1 (Deterministic)** | Active (default) | `run_controller()` | Plan → step loop → GoalEvaluator → Critic/RetryPlanner |
| **Mode 2 (Autonomous)** | Future | `run_autonomous()` | Goal-driven observe → decide → execute loop |
| **Multi-Agent (Phase 9)** | Implemented | `run_multi_agent()` | Supervisor → specialized role agents |

---

## Development Phases (1–16)

| Phase | Focus |
|-------|-------|
| 1 | Pipeline convergence |
| 2 | Integration |
| 3 | Scenario evaluation (40 tasks) |
| 4 | Reliability (failure policies, limits) |
| 5 | Capability expansion + attempt loop |
| 6 | Developer experience (CLI, chat, session) |
| 7 | Reliability hardening (timeouts, validation) |
| 8 | Autonomous mode (self-improving loop) |
| 9 | Multi-agent (hierarchical roles) |
| 10 | Repository intelligence |
| 10.5 | Graph-guided localization |
| 11 | Intelligence layer (learning from runs) |
| 12 | Developer workflow (issue → PR → CI → review) |
| 13 | Prompt infrastructure |
| 14 | Token budgeting & context control |
| 15 | Trajectory loop |
| 16 | Failure mining |