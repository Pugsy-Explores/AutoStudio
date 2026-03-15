# AutoStudio Configuration

All configuration values are centralized under the top-level `config/` directory. Each module supports environment variable overrides.

## Config Modules

| Module | Description |
|--------|-------------|
| `config/agent_config.py` | Agent loop and controller limits |
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
| ENABLE_CONTEXT_RANKING | True | ENABLE_CONTEXT_RANKING | 1/0 or true/false |
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
