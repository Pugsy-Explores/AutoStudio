# Repository Symbol Graph — Design Document

## Overview

Large coding agents use **repository structure awareness** to dramatically improve retrieval. Instead of discovering context purely dynamically (search → expand → read), they build a **symbol dependency graph** during indexing. Retrieval then becomes:

```
symbol → dependency graph → instant cross-file reasoning
```

Research systems (e.g. code understanding, program synthesis) use knowledge graphs or dependency graphs to improve code retrieval precision.

---

## Implementation Status

**Implemented.** AutoStudio includes:

- **repo_index/** — Tree-sitter parser, parallel indexing, symbol extraction, dependency extraction; optional embedding index (ChromaDB at `.symbol_graph/embeddings/`)
- **repo_graph/** — SQLite graph storage, 2-hop expansion; `repo_map_builder` (architectural map); `change_detector` (affected callers, risk levels)
- **agent/retrieval/** — `search_pipeline` (hybrid parallel retrieval); `symbol_expander` (expand_from_anchors: anchor → expand_symbol_dependencies → fetch bodies → rank → prune; max 15 symbols, 6 snippets); `graph_retriever` (symbol lookup → expansion); `vector_retriever` (embedding search); `retrieval_cache` (LRU); `context_builder` (build_call_chain_context for execution paths); `context_builder_v2` (assemble_reasoning_context: FILE/SYMBOL/LINES/SNIPPET, ~8000 chars)
- **editing/** — `diff_planner`, `patch_generator`, `ast_patcher`, `patch_validator`, `patch_executor`, `conflict_resolver`, `test_repair_loop`

### Retrieval flow

1. SEARCH → `repo_map_lookup.lookup_repo_map(query)` + `anchor_detector.detect_anchor(query, repo_map)` → `state.context["repo_map_anchor"]`, `state.context["repo_map_candidates"]`
2. `retrieval_cache.get_cached(query)` (if enabled)
3. **Hybrid retrieval** (when `ENABLE_HYBRID_RETRIEVAL=1`): `search_pipeline.hybrid_retrieve()` — when `repo_map_anchor` has confidence ≥ 0.9, graph retriever uses anchor symbol; runs graph, vector, grep in parallel; merges and returns top 20
4. **Sequential fallback**: `graph_retriever.retrieve_symbol_context(query)` → `vector_retriever.search_by_embedding(query)` → Serena `search_for_pattern`
5. **Symbol expansion** (when graph exists): `symbol_expander.expand_from_anchors` — anchor → `expand_symbol_dependencies` (BFS along calls/imports/references; depth=2, max_nodes=20, max_symbol_expansions=8) → fetch symbol bodies → rank → prune to 6 (max 15 symbols)
6. Retrieval expansion (capped at MAX_SYMBOL_EXPANSION=10) → `read_symbol_body`, `read_file`, `find_referencing_symbols` (structured: callers, callees, imports, referenced_by; cap 10 each)
7. Context builder → symbols, references, files; `build_call_chain_context` when project_root + symbols (execution path formatting)
8. Deduplication (unconditional) → candidate budget (MAX_RERANK_CANDIDATES=50)
9. Ranker (max 20 candidates) + pruner (max 6 snippets) → final context; `context_builder_v2.assemble_reasoning_context` formats for EXPLAIN

All retrieval budgets and flags are configurable via `config/retrieval_config.py`; see [CONFIGURATION.md](CONFIGURATION.md).

### Path normalization

Search results from graph, vector, Serena, or grep may occasionally contain malformed file paths (e.g. JSON artifacts like `{"path` from mis-parsed output). `retrieval_expander.normalize_file_path()` strips these artifacts before expansion. Relative paths are resolved against `project_root` in `_run_retrieval_expansion`. The agent loop sets `project_root` in context (from `SERENA_PROJECT_DIR` or cwd) so retrieval expansion can resolve paths correctly.

---

## Target Architecture

### Symbol Graph Schema

```
Symbol
  ├─ calls: [Symbol]        # functions/methods called
  ├─ imports: [Symbol]     # symbols imported
  ├─ referenced_by: [Symbol] # usages / references
  ├─ defined_in: File
  └─ type: function | class | method | ...
```

Example:

```
StepExecutor
   ├─ uses → dispatch
   ├─ uses → policy_engine
   └─ calls → run_tool
```

### Indexer

- **Input:** Repository root
- **Process:** Parse codebase (AST or language server), extract:
  - Symbol definitions (name, file, line, type)
  - Call edges (A calls B)
  - Import edges (A imports B)
  - Reference edges (A references B)
- **Output:** Graph stored in SQLite or compact JSON

### Retrieval Path

- **`find_referencing_symbols(symbol, file_path, project_root?) -> dict`**
  - Returns `{callers: [...], callees: [...], imports: [...], referenced_by: [...]}`; each list capped at 10
  - Uses GraphStorage when `.symbol_graph/index.sqlite` exists; O(1) lookup via `get_callers`, `get_callees`, `get_imports`, `get_referenced_by`
- **`expand_symbol_dependencies(symbol_id, storage, depth, max_nodes, max_symbol_expansions) -> (nodes, telemetry)`**
  - BFS along dependency edges; cycle-safe; returns telemetry for observability

---

## Implementation

### Indexing (repo_index)

- **Parser:** Tree-sitter for `.py` (`parse_file`); requires `tree-sitter` and `tree-sitter-python`
- **Symbol extraction:** Functions, classes, methods, modules; docstrings; type_info (params, return_type); signatures
- **Dependency extraction:** imports, calls, call_graph, inherits, references, control_flow, data_flow
- **CLI:** `python -m repo_index.index_repo <path>` — `-v`/`--verbose` logs each file; `--no-gitignore` disables .gitignore filtering
- **Gitignore:** Paths matching `.gitignore` (e.g. `venv/`, `.venv/`, `__pycache__/`) are excluded by default
- **API:** `index_repo(root_dir, output_dir=None, include_dirs=None, ignore_gitignore=True, verbose=False)` — `include_dirs` limits indexing to subdirs (e.g. `("agent", "editing")`) for faster partial indexing

### Graph (repo_graph)

- **Storage:** SQLite `nodes` (id, name, type, file, start_line, end_line, docstring, type_info, signature), `edges` (source_id, target_id, edge_type)
- **Query:** `find_symbol`, `expand_neighbors(depth=2)`, `expand_symbol_dependencies(symbol_id, storage, depth, max_nodes, max_symbol_expansions)`
- **Dependency helpers:** `get_callers`, `get_callees`, `get_imports`, `get_referenced_by` — filter by edge type (calls, call_graph, imports, references)

### Retrieval (search_pipeline, graph_retriever, vector_retriever)

- **search_pipeline:** `hybrid_retrieve(query, state)` — runs graph, vector, grep in parallel; merges and dedupes; returns top 20. Set `ENABLE_HYBRID_RETRIEVAL=0` for sequential fallback.
- **graph_retriever:** `retrieve_symbol_context(query, project_root)` → extracts symbol candidates (CamelCase, snake_case, tokens) → find_symbol → expand_neighbors(2) → cap at 15 symbols
- **vector_retriever:** `search_by_embedding(query)` — semantic search; uses ChromaDB at `.symbol_graph/embeddings/`
- **retrieval_cache:** LRU cache for search results (`RETRIEVAL_CACHE_SIZE`)
- **symbol_expander:** `expand_from_anchors(anchors, query, project_root)` — uses graph for context expansion; max 15 symbols, 6 snippets
- **context_builder_v2:** `assemble_reasoning_context(snippets, max_chars=8000)` — FILE/SYMBOL/LINES/SNIPPET format for reasoning
- **Budgets:** MAX_SEARCH_RESULTS=20, MAX_SYMBOL_EXPANSION=10, MAX_CONTEXT_SNIPPETS=6

### Repo map and change detector (repo_graph)

- **repo_map_builder:** `build_repo_map(project_root)` → high-level architectural map; `build_repo_map_from_storage(graph_storage)` → spec format `{modules: {}, symbols: {}, calls: {}}` → `repo_map.json`
- **repo_map_lookup:** `lookup_repo_map(query, project_root)` → tokenize query, match against `repo_map["symbols"]` → `[{anchor, file}, ...]`; `load_repo_map(project_root)` loads `repo_map.json`
- **anchor_detector:** `detect_anchor(query, repo_map)` → exact/fuzzy symbol match → `{symbol, confidence}` (1.0 exact, 0.9 fuzzy); seeds graph retrieval when confidence ≥ 0.9
- **repo_map_updater:** `update_repo_map_for_file(file_path, project_root)` → rebuilds `repo_map.json` from updated graph; call after `update_index_for_file`
- **change_detector:** `detect_change_impact(project_root, changed_files)` → affected files/symbols, risk level (LOW/MEDIUM/HIGH)

### Diff planner and editing (editing)

- `plan_diff(instruction, context)` → planned changes
- **patch_generator:** `to_structured_patches(plan, instruction, context)` → {file, patch: {symbol, action, target_node, code}}. Uses `_looks_like_code` heuristic to detect patch text as code (def, class, return, import, `=`, `logger.`, `print(`, newlines); otherwise falls back to instruction snippet.
- **ast_patcher:** Tree-sitter AST edits (insert at function_body_start, replace/delete function_body, statement-level, block-level)
- **patch_validator:** compile + AST reparse before write
- **patch_executor:** apply → validate → write; rollback on failure; max 5 files, 200 lines per patch
- **conflict_resolver:** same symbol, same file, semantic overlap → sequential groups
- **test_repair_loop:** run tests after patch; repair on failure (max 3 attempts); flaky detection; compile step

**Tests:** `tests/test_agent_e2e.py` (full pipeline), `tests/test_agent_trajectory.py` (complex trajectories), `tests/test_repo_map.py` (repo_map build, lookup, anchor detection, incremental update), `tests/test_patch_generator.py`, `tests/test_ast_patcher.py`, `tests/test_patch_validator.py`, `tests/test_patch_executor.py`, `tests/test_editing_pipeline.py`, `tests/test_diff_planner.py`

---

## Storage Format (SQLite)

```sql
CREATE TABLE nodes (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  name TEXT NOT NULL,
  type TEXT,           -- function, class, method, module
  file TEXT NOT NULL,
  start_line INTEGER,
  end_line INTEGER,
  docstring TEXT,
  type_info TEXT,      -- JSON: {params: {name: type}, return_type}
  signature TEXT       -- e.g. def foo(a: int) -> bool
);

CREATE TABLE edges (
  source_id INTEGER NOT NULL,
  target_id INTEGER NOT NULL,
  edge_type TEXT       -- calls, call_graph, imports, inherits, references, control_flow, data_flow
);

CREATE INDEX idx_nodes_name ON nodes(name);
CREATE INDEX idx_edges_source ON edges(source_id);
CREATE INDEX idx_edges_target ON edges(target_id);
```

Index location: `{repo_root}/.symbol_graph/index.sqlite`

---

## Testing and Validation

The indexing system is validated by `tests/test_indexer.py`, `tests/test_symbol_graph.py`, and `tests/test_retrieval_pipeline.py`:

| Test | Coverage |
|------|----------|
| `test_scan_repo_finds_all_python_files` | `scan_repo()` discovers and parses all `.py` files; AST trees cover every file |
| `test_index_repo_extracts_symbols_with_required_fields` | Every symbol has `symbol_name`, `file`, `start_line`, `end_line` |
| `test_dependency_extractor_finds_imports` | `dependency_extractor` produces import edges (e.g. `sub/mod.py` → `foo`) |
| `test_graph_builder_creates_nodes_and_edges` | `build_graph` stores nodes and edges; `nodes > 0`, `edges > 0`; nodes have `name`, `file`, `start_line`, `end_line` |
| `test_index_repo_on_fixtures_repo` | End-to-end: index `tests/fixtures/repo` → SQLite has nodes/edges → `find_symbol`, `expand_neighbors` work |
| `test_find_symbol_and_expand_neighbors_sqlite` | `find_symbol` and `expand_neighbors` against graph built by `index_repo` |
| `test_retrieval_pipeline_*` | Query → graph retrieval → context builder; symbols ≤15; fallback search when graph fails; requires `tree-sitter` |
| `test_symbol_expansion.py` | expand_from_anchors: no index, caps, integration with graph; no tree-sitter required for unit tests |

**Retrieval pipeline tests** (`test_retrieval_pipeline.py`): session-scoped index over `agent/` and `editing/`; skip when `tree-sitter-python` not installed; `-m "not slow"` excludes full-index tests.

**Fixtures:** `tests/fixtures/repo/` — `foo.py`, `typed_foo.py`, `sub/mod.py`, `sub/__init__.py` (sample functions, classes, imports, type hints).

**Run tests:**
```bash
INDEX_EMBEDDINGS=0 pytest tests/test_indexer.py tests/test_symbol_graph.py tests/test_retrieval_pipeline.py tests/test_graph_retriever.py -v
```

**Debug logging** (when failures occur):
```bash
INDEX_EMBEDDINGS=0 pytest tests/test_indexer.py tests/test_symbol_graph.py -v --log-cli-level=DEBUG
```

Logging covers: scan file count, edge extraction, graph node/edge counts, `find_symbol` misses, `expand_neighbors` invalid args.

---

## Benefits

| Benefit | Description |
|---------|-------------|
| **Latency** | Instant lookup vs. N MCP round-trips |
| **Coverage** | Full graph even when Serena is unavailable |
| **Cross-file** | Natural "expand to dependencies" in one step |
| **Precision** | Graph structure informs ranking (callers/callees) |

---

## References

- Knowledge graphs for code: CodeBERT, GraphCodeBERT, UniXcoder
- Dependency graphs: LSP symbol references, tree-sitter queries
- Serena MCP: `find_symbol`, `search_for_pattern` — can be complemented by precomputed graph
