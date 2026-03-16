# Retrieval Subsystem

Hybrid retrieval pipeline for code search: BM25 lexical, graph, vector, grep in parallel; RRF fusion; graph expansion; reference lookup; call-chain context; deduplication; cross-encoder reranking; context pruning.

## Purpose

Retrieval must always precede reasoning (Rule 2). This subsystem implements the immutable pipeline:

```
query rewrite → repo map lookup → anchor detection → parallel search (BM25, graph, vector, grep)
→ RRF fusion → graph expansion → reference lookup → call-chain context
→ deduplication → candidate budget → cross-encoder reranker → context pruning
```

## Architecture

| Module | Purpose |
|--------|---------|
| search_pipeline | `hybrid_retrieve()` — runs BM25, graph, vector, grep in parallel; merges via RRF |
| retrieval_pipeline | `run_retrieval_pipeline()` — orchestrates anchor → expand → build → dedup → rerank → prune |
| query_rewriter | Rewrites planner step into code-search query |
| anchor_detector | Identifies symbol/class/function matches |
| symbol_expander | Expands anchors via graph; `expand_symbol_dependencies` |
| graph_retriever | Symbol lookup + 2-hop expansion |
| bm25_retriever | BM25 lexical retrieval |
| rank_fusion | Reciprocal Rank Fusion |
| context_builder | Assembles symbols, references, call-chain context |
| context_ranker | Hybrid LLM+lexical scoring |
| context_pruner | Enforces snippet and char budget |
| reranker/ | Cross-encoder reranking (see [reranker/README.md](reranker/README.md)) |

## Key Classes

- `run_retrieval_pipeline(search_results, state, query)` — main entry point (called by dispatcher)
- `hybrid_retrieve(query, state)` — parallel search + RRF
- `expand_from_anchors()` — graph expansion with telemetry
- `build_context_from_symbols()` — includes `build_call_chain_context`
- `deduplicate_candidates()` — pre-rerank dedup
- `create_reranker()` — cross-encoder factory

## Data Flow

1. Dispatcher calls `_search_fn` → `hybrid_retrieve` (or cache hit)
2. `run_retrieval_pipeline(results, state, query)` receives merged results
3. Anchor detection → localization (optional) → symbol expansion → retrieval expansion
4. `find_referencing_symbols` → `build_context_from_symbols` (call-chain)
5. Deduplication → candidate budget → reranker (or LLM ranker fallback)
6. Context pruning → `state.context["ranked_context"]`

## See Also

- [Docs/RETRIEVAL_ARCHITECTURE.md](../../Docs/RETRIEVAL_ARCHITECTURE.md) — full pipeline details
- [Docs/ARCHITECTURE.md](../../Docs/ARCHITECTURE.md) — system overview
- [reranker/README.md](reranker/README.md) — cross-encoder subsystem
