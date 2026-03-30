# AutoStudio Configuration

All configuration values are centralized under the top-level `config/` directory. Each module supports environment variable overrides.

## Startup Bootstrap (config/startup.py)

When the main service starts (CLI `agent.cli.run_agent`), `ensure_services_ready()` runs before any agent work:

1. **Reranker**: If not running → log info and initialize (warm-start) before anything else. On failure, pipeline uses retriever-score ordering (no LLM fallback).
2. **LLM models**: Verify all endpoints from `task_models` are reachable. If any are unreachable → state error clearly and exit.

| Variable | Default | Description |
|----------|---------|-------------|
| SKIP_STARTUP_CHECKS | 0 | Set to 1 to bypass reranker init and LLM reachability check (e.g. tests with mocked services) |
| RERANKER_STARTUP | 1 | Auto-init reranker at service startup (default ON). Set to 0 to skip; reranker will lazy-load on first use. |

## Config Modules

| Module | Description |
|--------|-------------|
| `config/agent_config.py` | Agent loop and controller limits |
| `config/agent_runtime.py` | Edit/test/fix execution loop: attempts, patch limits, same-error guard, sandbox (see [Runtime safety](#agent_runtimepy-execution-loop)) |
| `config/editing_config.py` | Patch and file editing limits |
| `config/logging_config.py` | Log level and format |
| `config/observability_config.py` | Trace and observability settings |
| `config/repo_graph_config.py` | Repository symbol graph paths |
| `config/repo_intelligence_config.py` | Phase 10: repo scan, architecture map, impact analyzer, context compressor limits |
| `config/retrieval_config.py` | Retrieval pipeline budgets and flags |
| `config/router_config.py` | Instruction router settings |
| `config/tool_graph_config.py` | Tool graph enable/disable |

## Variables by Module

### agent_config.py

Used by `agent_controller`. `agent_loop` uses its own constants in `agent/orchestrator/agent_loop.py` (Phase 4: 60s runtime, 3 replans, 20 steps, 50 tool calls).

| Variable | Default | Env Override | Description |
|----------|---------|--------------|-------------|
| MAX_TASK_RUNTIME_SECONDS | 900 | MAX_TASK_RUNTIME_SECONDS | Max seconds before task times out |
| MAX_REPLAN_ATTEMPTS | 5 | MAX_REPLAN_ATTEMPTS | Max replan attempts on step failure |
| MAX_CONTEXT_CHARS | 32000 | MAX_CONTEXT_CHARS | Hard cap on context before LLM reasoning call |
| MAX_STEP_TIMEOUT_SECONDS | 15 | MAX_STEP_TIMEOUT_SECONDS | Per-step timeout (Phase 7) |
| MAX_FILES_PER_PR | 10 | MAX_FILES_PER_PR | Phase 12: max files per PR (safety) |
| MAX_PATCH_LINES | 500 | MAX_PATCH_LINES | Phase 12: max patch lines (safety) |
| MAX_CI_RUNTIME_SECONDS | 600 | MAX_CI_RUNTIME_SECONDS | Phase 12: CI timeout (pytest, ruff) in seconds |
| MAX_PROMPT_TOKENS | 12000 | MAX_PROMPT_TOKENS | Phase 14: hard cap on total prompt tokens before LLM call |
| OUTPUT_TOKEN_RESERVE | 2000 | OUTPUT_TOKEN_RESERVE | Phase 14: tokens reserved for model output |
| MAX_REPO_SNIPPETS | 10 | MAX_REPO_SNIPPETS | Phase 14: max ranked code snippets passed to prompt |
| MAX_HISTORY_TOKENS | 2000 | MAX_HISTORY_TOKENS | Phase 14: token budget for conversation history section |
| MAX_REPO_CONTEXT_TOKENS | 7200 | MAX_REPO_CONTEXT_TOKENS | Phase 14: threshold triggering conditional compression (60% of 12000) |
| MAX_RETRIEVAL_RESULTS | 20 | MAX_RETRIEVAL_RESULTS | Phase 14: max candidates passed from retrieval to ranker |
| HISTORY_WINDOW_TURNS | 10 | HISTORY_WINDOW_TURNS | Phase 14: last N turns kept verbatim in sliding window |
| HISTORY_SUMMARY_TURNS | 30 | HISTORY_SUMMARY_TURNS | Phase 14: turns 10–30 collapsed into one summarized memory block |
| MAX_RETRY_ATTEMPTS | 3 | MAX_RETRY_ATTEMPTS | Phase 15: max retry attempts in trajectory loop (autonomous mode) |
| MAX_RETRY_RUNTIME_SECONDS | 120 | MAX_RETRY_RUNTIME_SECONDS | Phase 15: max wall-clock seconds for retry loop before stopping |

### agent_loop constants (agent/orchestrator/agent_loop.py)

Phase 4 reliability limits; not configurable via env:

| Constant | Value | Description |
|----------|-------|-------------|
| MAX_REPLAN_ATTEMPTS | 3 | Max replan attempts on step failure |
| MAX_STEP_RETRIES | 2 | Retry same step before triggering replan |
| MAX_STEPS | 20 | Hard step count per task |
| MAX_TOOL_CALLS | 50 | Max tool invocations per task |
| MAX_TASK_RUNTIME_SECONDS | 60 | Wall-clock timeout per task |
| MAX_LOOP_ITERATIONS | 100 | Stall detection |
| MAX_STEP_TIMEOUT_SECONDS | 15 (from config) | Per-step timeout; Phase 7 |

### agent_runtime.py (execution loop)

Used by `agent/runtime/execution_loop.py`. Snapshot-based rollback (no git), syntax validation before tests, retry guard, strategy explorer only when retries exhausted.

| Variable | Default | Env Override | Description |
|----------|---------|--------------|-------------|
| REACT_MODE | 1 | REACT_MODE | **1** = ReAct (default): model chooses actions; run_hierarchical → execution_loop. **0** = legacy: run_attempt_loop, planner, GoalEvaluator, Critic, RetryPlanner. See [REACT_ARCHITECTURE.md](REACT_ARCHITECTURE.md). |
| MAX_EDIT_ATTEMPTS | 3 | MAX_EDIT_ATTEMPTS | Max attempts in edit→test→fix loop (legacy path) |
| MAX_PATCH_LINES | 300 | MAX_PATCH_LINES | Max total patch lines per attempt (reject before apply) |
| MAX_PATCH_FILES | 5 | MAX_PATCH_FILES | Max files per patch (reject before apply) |
| MAX_SAME_ERROR_RETRIES | 2 | MAX_SAME_ERROR_RETRIES | Stop after this many consecutive identical failure types |
| MAX_STRATEGIES | 3 | MAX_STRATEGIES | Max alternative strategies from strategy_explorer when retries exhausted |
| TEST_TIMEOUT | 120 | TEST_TIMEOUT | Test run timeout (seconds) |
| TRAJECTORY_STORE_ENABLED | True | TRAJECTORY_STORE_ENABLED | Persist trajectory (attempt, failure_type, patch, etc.) to TRAJECTORY_STORE_DIR |
| TRAJECTORY_STORE_DIR | data/trajectories | TRAJECTORY_STORE_DIR | Directory for trajectory JSONL |
| ENABLE_SANDBOX | False | ENABLE_SANDBOX | When 1/true: copy project to temp dir for patch + tests; no host filesystem modification |

### editing_config.py

| Variable | Default | Env Override | Description |
|----------|---------|--------------|-------------|
| MAX_PATCH_SIZE | 200 | MAX_PATCH_SIZE | Max lines per patch |
| MAX_FILES_EDITED | 5 | MAX_FILES_EDITED | Max files per edit step |

### logging_config.py

| Variable | Default | Env Override | Description |
|----------|---------|--------------|-------------|
| LOG_LEVEL | INFO | LOG_LEVEL | Python logging level |
| LOG_FORMAT | %(message)s | LOG_FORMAT | Log message format |

### observability_config.py

| Variable | Default | Env Override | Description |
|----------|---------|--------------|-------------|
| AGENT_MEMORY_DIR | .agent_memory | AGENT_MEMORY_DIR | Base dir for agent memory |
| TRACES_SUBDIR | traces | TRACES_SUBDIR | Subdir for trace files |
| MAX_TRACE_SIZE_BYTES | 512000 | MAX_TRACE_SIZE_BYTES | Max trace file size before truncation |

`get_trace_dir(project_root)` returns the full path to `.agent_memory/traces/`.

**Phase 11 intelligence layer** uses additional subdirs under `.agent_memory/`:
- `solutions/` — successful solution JSON files (goal, files_modified, patch_summary)
- `intelligence_index/` — ChromaDB vector index for solution pattern search
- `developer_profile.json` — developer preferences learned from accepted solutions
- `repo_knowledge.json` — frequent_bug_areas, common_refactor_patterns, architecture_constraints

**Phase 12 workflow layer** uses:
- `last_workflow.json` — last workflow result (task, pr, ci, review, patches) for `autostudio pr` and `autostudio review` commands

**Phase 13 prompt infrastructure** uses:
- `dev/prompt_eval_results/` — prompt CI output (e.g. `planner_v1.json`); `baseline.json` for regression comparison
- `dev/failure_logs/{prompt_name}/{date}.jsonl` — failure records from `failure_logger.log_failure()`

**Phase 15 trajectory loop** uses:
- `.agent_memory/trajectories/<task_id>.json` — per-task trajectory records: goal, attempts (each with attempt, start_time, end_time, steps, evaluation, diagnosis, strategy), final_status, timestamp

### repo_graph_config.py

| Variable | Default | Env Override | Description |
|----------|---------|--------------|-------------|
| SYMBOL_GRAPH_DIR | .symbol_graph | SYMBOL_GRAPH_DIR | Symbol graph directory |
| REPO_MAP_JSON | repo_map.json | REPO_MAP_JSON | Repo map filename |
| SYMBOLS_JSON | symbols.json | SYMBOLS_JSON | Symbols index filename |
| INDEX_SQLITE | index.sqlite | INDEX_SQLITE | Graph index filename |
| MAX_EXPANSION_DEPTH | 2 | GRAPH_EXPANSION_DEPTH | Graph expansion depth |

`get_repo_map_path(project_root)` returns the path to `repo_map.json`.

### repo_intelligence_config.py (Phase 10)

| Variable | Default | Env Override | Description |
|----------|---------|--------------|-------------|
| MAX_REPO_SCAN_FILES | 200 | MAX_REPO_SCAN_FILES | Cap on files scanned for repo summary |
| MAX_ARCHITECTURE_NODES | 500 | MAX_ARCHITECTURE_NODES | Cap on modules in architecture map |
| MAX_CONTEXT_TOKENS | 8192 | MAX_CONTEXT_TOKENS | Context budget for compressor (chars ≈ 4×tokens) |
| MAX_IMPACT_DEPTH | 3 | MAX_IMPACT_DEPTH | BFS depth for impact analyzer |

### retrieval_config.py

| Variable | Default | Env Override | Description |
|----------|---------|--------------|-------------|
| MAX_CONTEXT_SNIPPETS | 6 | MAX_CONTEXT_SNIPPETS | Max snippets in context |
| DEFAULT_MAX_SNIPPETS | 6 | DEFAULT_MAX_SNIPPETS | Default max snippets |
| DEFAULT_MAX_CHARS | 8000 | DEFAULT_MAX_CHARS | Default char budget |
| DEFAULT_MAX_CONTEXT_CHARS | 16000 | DEFAULT_MAX_CONTEXT_CHARS | Context builder char cap |
| MAX_SEARCH_RESULTS | 20 | MAX_SEARCH_RESULTS | Max search results |
| MAX_SYMBOL_EXPANSION | 10 | MAX_SYMBOL_EXPANSION | Max symbols to expand |
| GRAPH_EXPANSION_DEPTH | 2 | GRAPH_EXPANSION_DEPTH | Graph expansion depth |
| ENABLE_HYBRID_RETRIEVAL | True | ENABLE_HYBRID_RETRIEVAL | 1/0 or true/false |
| ENABLE_VECTOR_SEARCH | True | ENABLE_VECTOR_SEARCH | 1/0 or true/false |
| ENABLE_CONTEXT_RANKING | True | ENABLE_CONTEXT_RANKING | 1/0 or true/false (unused by retrieval pipeline; kept for compatibility) |
| RETRIEVAL_CACHE_SIZE | 100 | RETRIEVAL_CACHE_SIZE | Retrieval cache size |
| MAX_CANDIDATES_FOR_RANKING | 20 | MAX_CANDIDATES_FOR_RANKING | Max candidates for LLM ranking |
| MAX_SNIPPET_CHARS_IN_BATCH | 400 | MAX_SNIPPET_CHARS_IN_BATCH | Per-snippet truncation in batch |
| FALLBACK_TOP_N | 3 | FALLBACK_TOP_N | Fallback when no anchors |
| MAX_SYMBOLS | 15 | MAX_SYMBOLS | Max symbols in expansion |
| MAX_RETRIEVED_SYMBOLS | 15 | MAX_RETRIEVED_SYMBOLS | Max symbols from graph |
| MAX_GRAPH_DEPTH | 3 | MAX_GRAPH_DEPTH | Phase 10.5: dependency traversal depth |
| MAX_DEPENDENCY_NODES | 100 | MAX_DEPENDENCY_NODES | Phase 10.5: cap on graph nodes |
| MAX_EXECUTION_PATHS | 10 | MAX_EXECUTION_PATHS | Phase 10.5: cap on execution path chains |
| ENABLE_LOCALIZATION_ENGINE | True | ENABLE_LOCALIZATION_ENGINE | Phase 10.5: graph-guided localization (1/0 or true/false) |
| ENABLE_BM25_SEARCH | True | ENABLE_BM25_SEARCH | Phase 17: BM25 lexical retrieval toggle |
| BM25_TOP_K | 30 | BM25_TOP_K | Phase 17: BM25 result count |
| ENABLE_RRF_FUSION | True | ENABLE_RRF_FUSION | Phase 17: Reciprocal Rank Fusion toggle |
| RRF_TOP_N | 100 | RRF_TOP_N | Phase 17: RRF merged result cap |
| RRF_K | 60 | RRF_K | Phase 17: RRF constant |
| RERANKER_ENABLED | True | RERANKER_ENABLED | Phase 17: cross-encoder reranker on/off |
| RERANKER_STARTUP | True | RERANKER_STARTUP | Phase 17: auto-init reranker at service startup (default ON). 0 = lazy-load on first use |
| RERANKER_USE_INT8 | True | RERANKER_USE_INT8 | Phase 17: use ONNX INT8 for both CPU and GPU (default ON). 0 = GPU uses sentence-transformers FP16 |
| RERANKER_DAEMON_PORT | 9004 | RERANKER_DAEMON_PORT | Retrieval daemon HTTP port (scripts/retrieval_daemon.py; reranker + embedding) |
| RETRIEVAL_DAEMON_PORT | 9004 | RETRIEVAL_DAEMON_PORT | Same as RERANKER_DAEMON_PORT; unified daemon port |
| RERANKER_USE_DAEMON | True | RERANKER_USE_DAEMON | Prefer retrieval daemon for reranker when reachable (default ON) |
| EMBEDDING_USE_DAEMON | True | EMBEDDING_USE_DAEMON | Prefer retrieval daemon /embed for vector search when reachable (default ON) |
| RETRIEVAL_DAEMON_AUTO_START | True | RETRIEVAL_DAEMON_AUTO_START | Start retrieval daemon if not running when agent starts (default ON). 0 = never start; user runs daemon manually |
| RETRIEVAL_DAEMON_START_TIMEOUT_SECONDS | 90 | RETRIEVAL_DAEMON_START_TIMEOUT_SECONDS | Max seconds to wait for daemon to become healthy after auto-start |
| RERANKER_DEVICE | auto | RERANKER_DEVICE | Phase 17: auto \| cpu \| gpu |
| RERANKER_GPU_MODEL | (from models_config.json reranker.gpu_model) | RERANKER_GPU_MODEL | Phase 17: GPU model ID |
| RERANKER_CPU_MODEL | (from models_config.json reranker.cpu_model) | RERANKER_CPU_MODEL | Phase 17: CPU ONNX model path |
| RERANKER_CPU_TOKENIZER | (from models_config.json reranker.cpu_tokenizer) | RERANKER_CPU_TOKENIZER | Phase 17: HuggingFace tokenizer ID for CPU ONNX |
| RERANKER_TOP_K | 10 | RERANKER_TOP_K | Phase 17: reranker output size |
| RERANKER_BATCH_SIZE | 16 | RERANKER_BATCH_SIZE | Phase 17: batch size for inference |
| RERANK_MIN_CANDIDATES | 6 | RERANK_MIN_CANDIDATES | Phase 17: min candidates to trigger reranker |
| MAX_RERANK_CANDIDATES | 50 | MAX_RERANK_CANDIDATES | Phase 17: cap candidates before reranker |
| MAX_RERANK_SNIPPET_TOKENS | 256 | MAX_RERANK_SNIPPET_TOKENS | Phase 17: per-snippet token limit |
| MAX_RERANK_PAIR_TOKENS | 512 | MAX_RERANK_PAIR_TOKENS | Phase 17: query+snippet pair limit |
| RERANK_CACHE_SIZE | 2048 | RERANK_CACHE_SIZE | Phase 17: LRU score cache size |
| RERANK_SCORE_THRESHOLD | 0.15 | RERANK_SCORE_THRESHOLD | Phase 17: discard results below this score |
| RERANK_MIN_RESULTS_AFTER_THRESHOLD | 3 | RERANK_MIN_RESULTS_AFTER_THRESHOLD | Phase 17: fallback if fewer pass threshold |
| RERANK_BATCH_WINDOW_MS | 5 | RERANK_BATCH_WINDOW_MS | Phase 17: RerankQueue coalescing window (ms) |
| RERANK_FUSION_WEIGHT | 0.8 | RERANK_FUSION_WEIGHT | Phase 17: reranker weight in score fusion |
| RETRIEVER_FUSION_WEIGHT | 0.2 | RETRIEVER_FUSION_WEIGHT | Phase 17: retriever weight in score fusion |
| SCORE_FUSION_RERANKER_WEIGHT | 0.8 | SCORE_FUSION_RERANKER_WEIGHT | Phase 17: reranker weight in fusion |
| SCORE_FUSION_RETRIEVER_WEIGHT | 0.2 | SCORE_FUSION_RETRIEVER_WEIGHT | Phase 17: retriever weight in fusion |
| RETRIEVAL_GRAPH_EXPANSION_DEPTH | 2 | RETRIEVAL_GRAPH_EXPANSION_DEPTH | Phase 18: graph expansion BFS depth |
| RETRIEVAL_GRAPH_MAX_NODES | 20 | RETRIEVAL_GRAPH_MAX_NODES | Phase 18: cap on expanded nodes |
| RETRIEVAL_MAX_SYMBOL_EXPANSIONS | 8 | RETRIEVAL_MAX_SYMBOL_EXPANSIONS | Phase 18: cap on symbol expansions |

See [RETRIEVAL_ARCHITECTURE.md](RETRIEVAL_ARCHITECTURE.md) for the full reranker config (models, cache, preprocessor, etc.).

### router_config.py

| Variable | Default | Env Override | Description |
|----------|---------|--------------|-------------|
| ENABLE_INSTRUCTION_ROUTER | True | ENABLE_INSTRUCTION_ROUTER | Route instruction before planner; CODE_SEARCH/CODE_EXPLAIN/INFRA skip planner |
| ROUTER_TYPE | "" | ROUTER_TYPE | baseline, fewshot, ensemble, or final |
| ROUTER_CONFIDENCE_THRESHOLD | 0.7 | ROUTER_CONFIDENCE_THRESHOLD | Confidence threshold |

### tool_graph_config.py

| Variable | Default | Env Override | Description |
|----------|---------|--------------|-------------|
| ENABLE_TOOL_GRAPH | True | ENABLE_TOOL_GRAPH | 1/0 or true/false |

## Environment Override Examples

```bash
# Increase context snippets
export MAX_CONTEXT_SNIPPETS=8

# Disable hybrid retrieval (sequential fallback)
export ENABLE_HYBRID_RETRIEVAL=0

# Disable instruction router (default: enabled)
export ENABLE_INSTRUCTION_ROUTER=0

# Use baseline router
export ROUTER_TYPE=baseline

# Increase task timeout to 30 minutes
export MAX_TASK_RUNTIME_SECONDS=1800

# Debug logging
export LOG_LEVEL=DEBUG

# Run integration tests (real services, no mocks)
export TEST_MODE=integration
# Then: pytest tests/integration/ -v
```

## Validation

`config/config_validator.py` runs at agent startup and asserts:

- MAX_CONTEXT_SNIPPETS > 0
- MAX_SEARCH_RESULTS > 0
- MAX_SYMBOL_EXPANSION >= 1
- GRAPH_EXPANSION_DEPTH >= 1
- MAX_FILES_EDITED > 0
- MAX_PATCH_SIZE > 0
- MAX_REPLAN_ATTEMPTS >= 1
- MAX_TASK_RUNTIME_SECONDS > 0

If any assertion fails, the agent exits with an error.
