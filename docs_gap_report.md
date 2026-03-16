# AutoStudio Documentation Gap Report

Generated as part of the documentation audit. Compares documentation against the current codebase to identify drift.

## Missing Documentation

| Gap | Description |
|-----|--------------|
| Docs/ARCHITECTURE.md | No single authoritative architecture document. Architecture is scattered across README.md, CODING_AGENT_ARCHITECTURE_GUIDE.md, and RETRIEVAL_ARCHITECTURE.md. |
| Docs/OBSERVABILITY.md | Telemetry fields and metrics are not documented in a standalone reference. Trace events appear in AGENT_CONTROLLER.md; retrieval metrics are mentioned in RETRIEVAL_ARCHITECTURE.md but not comprehensively. |
| agent/retrieval/README.md | No module-level README for the retrieval subsystem. |
| agent/retrieval/reranker/README.md | No module-level README for the cross-encoder reranker subsystem. |
| agent/meta/README.md | No module-level README for trajectory loop, critic, retry planner. |
| agent/failure_mining/README.md | No module-level README for failure mining subsystem. |

## Outdated Documentation

| Document | Issue |
|----------|-------|
| README.md | Pipeline diagram does not include: BM25 lexical retrieval, RRF rank fusion, graph expansion helpers (get_callers/callees/imports/referenced_by), reference lookup (find_referencing_symbols), call-chain context (build_call_chain_context), deduplication stage, cross-encoder reranker (Qwen3-Reranker-0.6B). SearchPath shows GraphRetriever, VectorRetriever, SerenaGrep but not BM25 or RRF. |
| README.md | SEARCH pipeline description mentions "hybrid retrieval (parallel graph + vector + grep)" but omits BM25 and RRF. Does not mention deduplication or reranker. |
| README.md | Tools table lists `find_referencing_symbols` as "Stub; wire to Serena when available" — code now uses symbol graph (reference_tools.py). |
| README.md | Project structure lists `agent/retrieval/vector_retriever.py` but does not list `agent/retrieval/bm25_retriever.py`, `agent/retrieval/rank_fusion.py`, or `agent/retrieval/reranker/`. |

## Incorrect Architecture Diagrams

| Document | Issue |
|----------|-------|
| README.md (Mermaid) | SearchPath subgraph shows RepoMapLookup → AnchorDetector → SearchPipeline → GraphRetriever, VectorRetriever, SerenaGrep. Missing: BM25, RRF fusion, deduplication, cross-encoder reranker. Post-SEARCH shows AnchorDetector → LocalizationEngine → SymbolExpander → Expand → ContextBuilder → Ranker → Pruner. Missing: reference lookup, call-chain context, deduplication, reranker. |
| README.md (ASCII) | Same omissions as Mermaid. |

## Undocumented Configuration

| Config Key | Module | Default | Status |
|------------|--------|---------|--------|
| RERANKER_GPU_MODEL | retrieval_config | Qwen/Qwen3-Reranker-0.6B | In CONFIGURATION.md via RETRIEVAL_ARCHITECTURE link; not in retrieval_config table |
| RERANKER_CPU_MODEL | retrieval_config | models/reranker/qwen3_reranker_int8.onnx | Same |
| MAX_RERANK_SNIPPET_TOKENS | retrieval_config | 256 | Not in CONFIGURATION.md |
| MAX_RERANK_PAIR_TOKENS | retrieval_config | 512 | Not in CONFIGURATION.md |
| RERANK_CACHE_SIZE | retrieval_config | 2048 | Not in CONFIGURATION.md |
| MAX_RERANK_CANDIDATES | retrieval_config | 50 | Not in CONFIGURATION.md |
| RETRIEVAL_GRAPH_EXPANSION_DEPTH | retrieval_config | 2 | Not in CONFIGURATION.md |
| RETRIEVAL_GRAPH_MAX_NODES | retrieval_config | 20 | Not in CONFIGURATION.md |
| RETRIEVAL_MAX_SYMBOL_EXPANSIONS | retrieval_config | 8 | Not in CONFIGURATION.md |
| RERANK_FUSION_WEIGHT | retrieval_config | 0.8 | Not in CONFIGURATION.md |
| RETRIEVER_FUSION_WEIGHT | retrieval_config | 0.2 | Not in CONFIGURATION.md |

## Undocumented Metrics

| Metric | Location | Meaning |
|--------|----------|---------|
| rerank_latency_ms | state.context["retrieval_metrics"] | Reranker inference time in ms |
| rerank_cache_hits | retrieval_metrics | Score cache hits |
| rerank_cache_misses | retrieval_metrics | Score cache misses |
| rerank_dedup_removed | retrieval_metrics | Candidates removed by pre-rerank dedup |
| rerank_tokens | retrieval_metrics | Approximate tokens in rerank batch |
| rerank_skipped_reason | retrieval_metrics | Why reranker was skipped |
| rerank_position_changes | retrieval_metrics | Position changes after rerank |
| rerank_avg_rank_shift | retrieval_metrics | Average rank shift |
| rerank_top1_changed | retrieval_metrics | Whether top-1 result changed |
| graph_nodes_expanded | retrieval_metrics | Nodes expanded in graph stage |
| graph_edges_traversed | retrieval_metrics | Edges traversed |
| graph_expansion_depth_used | retrieval_metrics | Depth used |
| graph_stage_skipped | retrieval_metrics | True when index absent |
| dedupe_removed_count | retrieval_metrics | Candidates removed by deduplicator |
| candidate_budget_applied | retrieval_metrics | Candidates trimmed by MAX_RERANK_CANDIDATES |

RETRIEVAL_ARCHITECTURE.md mentions some of these in a telemetry table; no standalone OBSERVABILITY.md exists.

## Undocumented Subsystems

| Subsystem | Status |
|-----------|--------|
| agent/retrieval/reranker/ | Documented in RETRIEVAL_ARCHITECTURE.md (phases 4.1–4.8) but no dedicated README. |
| agent/retrieval/localization/ | Documented in phase_10-5 and REPOSITORY_SYMBOL_GRAPH; no module README. |
| agent/meta/ (trajectory loop) | Documented in phase_8, phase_15, AGENT_LOOP_WORKFLOW; no module README. |
| agent/failure_mining/ | Documented in FAILURE_MINING.md; no module README. |

## Summary

- **6 missing documents** (ARCHITECTURE, OBSERVABILITY, 4 component READMEs)
- **README.md** pipeline diagram and descriptions are outdated (Phases 17/18)
- **CONFIGURATION.md** retrieval_config table is incomplete (Phase 17/18 keys)
- **Telemetry** fields are partially documented in RETRIEVAL_ARCHITECTURE.md but not in a dedicated observability reference
