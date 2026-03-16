# Phase 18 — Retrieval Precision Upgrade

## Goal

Extend the AutoStudio retrieval pipeline with ten precision upgrades: graph dependency helpers, reference lookup, reranker test coverage, call-chain context builder, explicit deduplication, graph expansion budget, candidate budget, graph telemetry, graph index fallback, and RRF rank fusion tests. All extensions preserve backward compatibility and the existing pipeline architecture.

## Architecture

```
anchor_detector
    ↓
graph_stage_skipped check (skip when index absent)
    ↓
symbol_expander (expand_symbol_dependencies: calls, imports, references)
    ↓
retrieval_expander (read_symbol_body / read_file)
    ↓
find_referencing_symbols (callers, callees, imports, referenced_by; cap 10 each)
    ↓
context_builder (build_call_chain_context when project_root + symbols)
    ↓
deduplicate_candidates (unconditional; SHA-256 snippet key)
    ↓
candidate_budget (slice to MAX_RERANK_CANDIDATES)
    ↓
cross-encoder reranker
    ↓
context_pruner → state.context
```

## Implemented Components

### repo_graph/graph_query.py

- **get_callers(symbol_id, storage)** — Nodes that call this symbol (incoming calls)
- **get_callees(symbol_id, storage)** — Nodes this symbol calls (outgoing calls)
- **get_imports(symbol_id, storage)** — Nodes this symbol imports
- **get_referenced_by(symbol_id, storage)** — Nodes that reference this symbol
- **expand_symbol_dependencies(symbol_id, storage, depth, max_nodes, max_symbol_expansions)** — BFS along dependency edges; cycle-safe; returns `(nodes, telemetry)` with `graph_nodes_expanded`, `graph_edges_traversed`, `graph_expansion_depth_used`

### agent/tools/reference_tools.py

- **find_referencing_symbols(symbol, file_path, project_root?)** — Returns `{callers, callees, imports, referenced_by}`; each list capped at 10; uses GraphStorage when index exists

### agent/retrieval/context_builder.py

- **build_call_chain_context(symbol, project_root)** — Formats execution paths as `symbol()\n  calls callee1()\n  calls callee2()`
- **build_context_from_symbols(..., project_root?)** — Injects call_chain when project_root and symbols present

### agent/retrieval/retrieval_pipeline.py

- **Graph index fallback** — Probes `.symbol_graph/index.sqlite`; sets `graph_stage_skipped=True` when absent; skips symbol_expander
- **Unconditional deduplication** — `deduplicate_candidates()` before reranker gate; `dedupe_removed_count` in telemetry
- **Candidate budget** — `candidates[:MAX_RERANK_CANDIDATES]`; `candidate_budget_applied` in telemetry
- **Reference merge** — Structured `find_referencing_symbols` output merged into candidate pool

### config/retrieval_config.py

- `RETRIEVAL_GRAPH_EXPANSION_DEPTH` (2)
- `RETRIEVAL_GRAPH_MAX_NODES` (20)
- `RETRIEVAL_MAX_SYMBOL_EXPANSIONS` (8)
- `MAX_RERANK_CANDIDATES` (50)

### Telemetry (state.context["retrieval_metrics"])

- `graph_nodes_expanded`, `graph_edges_traversed`, `graph_expansion_depth_used`
- `graph_stage_skipped` (bool)
- `dedupe_removed_count`, `candidate_count`, `candidate_budget_applied`

## Tests

- **tests/test_graph_expansion.py** — get_callers, get_callees, get_imports, get_referenced_by; expand_symbol_dependencies (max_nodes, max_symbol_expansions, cycles, telemetry)
- **tests/test_reference_lookup.py** — find_referencing_symbols (no index, with graph, cap 10, symbol not found)
- **tests/test_call_chain_context.py** — build_call_chain_context; build_context_from_symbols injection
- **tests/test_candidate_deduplication.py** — deduplicate_candidates (exact dupes, order, whitespace)
- **tests/test_graph_expansion_budget.py** — RETRIEVAL_MAX_SYMBOL_EXPANSIONS; max_nodes cap
- **tests/test_candidate_budget.py** — MAX_RERANK_CANDIDATES config and slice logic
- **tests/test_graph_telemetry.py** — graph_nodes_expanded, graph_edges_traversed, graph_expansion_depth_used
- **tests/test_graph_fallback.py** — graph_stage_skipped when index absent; pipeline continues
- **tests/test_rank_fusion.py** — RRF (multi-list ranking, top_n, dedup, empty/single list)

## Validation

```bash
pytest tests/test_graph_expansion.py tests/test_reference_lookup.py \
       tests/test_reranker.py tests/test_call_chain_context.py \
       tests/test_candidate_deduplication.py \
       tests/test_graph_expansion_budget.py tests/test_candidate_budget.py \
       tests/test_graph_telemetry.py tests/test_graph_fallback.py \
       tests/test_rank_fusion.py

python scripts/run_retrieval_eval.py
```

## Docs

- [RETRIEVAL_ARCHITECTURE.md](../../Docs/RETRIEVAL_ARCHITECTURE.md) — Section 2.5 Graph Dependency Expansion and Reference Lookup; config reference; telemetry
- [REPOSITORY_SYMBOL_GRAPH.md](../../Docs/REPOSITORY_SYMBOL_GRAPH.md) — Retrieval flow steps 5–9; graph query helpers
- [AGENT_LOOP_WORKFLOW.md](../../Docs/AGENT_LOOP_WORKFLOW.md) — Retrieval pipeline description

## Status

**COMPLETED** — All ten upgrades implemented; 73 retrieval-related tests pass.
