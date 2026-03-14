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
- **agent/retrieval/** — `graph_retriever` (symbol lookup → expansion); `vector_retriever` (embedding search); `retrieval_cache` (LRU)
- **editing/** — `diff_planner`, `patch_generator`, `ast_patcher`, `patch_validator`, `patch_executor`, `conflict_resolver`, `test_repair_loop`

### Retrieval flow

1. SEARCH → `retrieval_cache.get_cached(query)` (if enabled)
2. `graph_retriever.retrieve_symbol_context(query)` (when `.symbol_graph/index.sqlite` exists)
3. If no results → `vector_retriever.search_by_embedding(query)` (when `ENABLE_VECTOR_SEARCH` and embeddings index exists)
4. Fallback → Serena `find_symbol` / `search_for_pattern` (tool graph: retrieve_graph → retrieve_vector → retrieve_grep → list_dir; query rewriter can set chosen_tool; set `SERENA_GREP_FALLBACK=0` to disable ripgrep)
5. Retrieval expansion → `read_symbol_body`, `read_file`, `find_referencing_symbols`
6. Context builder → symbols, references, files
7. Ranker + pruner → final context

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

- **`get_symbol_dependencies(symbol: str) -> dict`**
  - Returns `{calls: [...], imported_by: [...], referenced_by: [...]}`
  - O(1) lookup when index exists
- **Integration:** When `find_referencing_symbols` is called and an index exists, use precomputed graph instead of runtime MCP call

---

## Implementation

### Indexing (repo_index)

- **Parser:** Tree-sitter for `.py` (`parse_file`); requires `tree-sitter` and `tree-sitter-python`
- **Symbol extraction:** Functions, classes, methods, modules; docstrings; type_info (params, return_type); signatures
- **Dependency extraction:** imports, calls, call_graph, inherits, references, control_flow, data_flow
- **CLI:** `python -m repo_index.index_repo <path>`
- **API:** `index_repo(root_dir, output_dir=None, include_dirs=None)` — `include_dirs` limits indexing to subdirs (e.g. `("agent", "editing")`) for faster partial indexing

### Graph (repo_graph)

- **Storage:** SQLite `nodes` (id, name, type, file, start_line, end_line, docstring, type_info, signature), `edges` (source_id, target_id, edge_type)
- **Query:** `find_symbol`, `expand_neighbors(depth=2)`

### Retrieval (graph_retriever, vector_retriever)

- **graph_retriever:** `retrieve_symbol_context(query, project_root)` → extracts symbol candidates from natural language (CamelCase, snake_case, tokens) → find_symbol → expand_neighbors(2) → cap at 15 symbols
- **vector_retriever:** `search_by_embedding(query)` — semantic search when graph returns nothing; uses ChromaDB at `.symbol_graph/embeddings/`
- **retrieval_cache:** LRU cache for search results (`RETRIEVAL_CACHE_SIZE`)
- Fallback to `search_code` when no index or no matches

### Repo map and change detector (repo_graph)

- **repo_map_builder:** `build_repo_map(project_root)` → high-level architectural map (modules, dependencies) → `repo_map.json`
- **change_detector:** `detect_change_impact(project_root, changed_files)` → affected files/symbols, risk level (LOW/MEDIUM/HIGH)

### Diff planner and editing (editing)

- `plan_diff(instruction, context)` → planned changes
- **patch_generator:** `to_structured_patches(plan, instruction, context)` → {file, patch: {symbol, action, target_node, code}}. Uses `_looks_like_code` heuristic to detect patch text as code (def, class, return, import, `=`, `logger.`, `print(`, newlines); otherwise falls back to instruction snippet.
- **ast_patcher:** Tree-sitter AST edits (insert at function_body_start, replace/delete function_body, statement-level, block-level)
- **patch_validator:** compile + AST reparse before write
- **patch_executor:** apply → validate → write; rollback on failure; max 5 files, 200 lines per patch
- **conflict_resolver:** same symbol, same file, semantic overlap → sequential groups
- **test_repair_loop:** run tests after patch; repair on failure (max 3 attempts); flaky detection; compile step

**Tests:** `tests/test_agent_e2e.py` (full pipeline), `tests/test_agent_trajectory.py` (complex trajectories), `tests/test_patch_generator.py`, `tests/test_ast_patcher.py`, `tests/test_patch_validator.py`, `tests/test_patch_executor.py`, `tests/test_editing_pipeline.py`, `tests/test_diff_planner.py`

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
