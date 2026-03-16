# AutoStudio System Components Inventory

Generated as part of the documentation audit. Lists major subsystems with file locations, purpose, and key entry points.

## Retrieval Pipeline

| Component | File Location | Purpose | Key Entry Points |
|-----------|---------------|---------|------------------|
| Retrieval Pipeline Orchestrator | agent/retrieval/retrieval_pipeline.py | Coordinates anchor detection, expansion, context building, reranker, pruning; emits telemetry | `run_retrieval_pipeline(search_results, state, query)` |
| Hybrid Search Pipeline | agent/retrieval/search_pipeline.py | Runs BM25, graph, vector, grep in parallel; merges via RRF or concat | `hybrid_retrieve(query, state)` |
| Query Rewriter | agent/retrieval/query_rewriter.py | Rewrites planner step into code-search query (LLM or heuristic) | `rewrite_query()`, `rewrite_query_with_context()` |
| Anchor Detector | agent/retrieval/anchor_detector.py | Identifies symbol/class/function matches; fallback to top-N | `detect_anchors()`, `detect_anchor()` |
| Symbol Expander | agent/retrieval/symbol_expander.py | Expands anchor symbols via graph; fetches bodies; ranks and prunes | `expand_from_anchors()` |
| Graph Retriever | agent/retrieval/graph_retriever.py | Symbol lookup + 2-hop expansion from SQLite index | `retrieve_symbol_context()` |
| BM25 Retriever | agent/retrieval/bm25_retriever.py | BM25Okapi lexical retrieval; lazy-built index | `search_bm25()`, `build_bm25_index()` |
| Rank Fusion | agent/retrieval/rank_fusion.py | Reciprocal Rank Fusion for merging result lists | `reciprocal_rank_fusion()` |
| Context Builder | agent/retrieval/context_builder.py | Deduplicates and assembles symbols/references/file snippets; call-chain context | `build_context_from_symbols()`, `build_call_chain_context()` |
| Context Builder V2 | agent/retrieval/context_builder_v2.py | Formats context in FILE/SYMBOL/LINES/SNIPPET blocks | `assemble_reasoning_context()` |
| Context Ranker | agent/retrieval/context_ranker.py | Hybrid LLM+lexical scoring; diversity penalty | `rank_context()` |
| Context Pruner | agent/retrieval/context_pruner.py | Enforces max snippet count and character budget | `prune_context()` |
| Repo Map Lookup | agent/retrieval/repo_map_lookup.py | Token matcher against repo_map.json symbols | `lookup_repo_map()` |
| Retrieval Expander | agent/retrieval/retrieval_expander.py | Converts search results to read_file/read_symbol_body actions | `expand_search_results()` |
| Retrieval Cache | agent/retrieval/retrieval_cache.py | LRU cache for (query, project_root) → results | `get_cached()`, `set_cached()` |

## Reranker Subsystem (agent/retrieval/reranker/)

| Component | File Location | Purpose | Key Entry Points |
|-----------|---------------|---------|------------------|
| Base Reranker | agent/retrieval/reranker/base_reranker.py | Abstract base; cache, adaptive gating, preprocessing | `BaseReranker.rerank()`, `_score_pairs()` |
| CPU Reranker | agent/retrieval/reranker/cpu_reranker.py | ONNX INT8 reranker (Qwen3-Reranker-0.6B) | `CPUReranker._score_pairs()` |
| GPU Reranker | agent/retrieval/reranker/gpu_reranker.py | Sentence-transformers CrossEncoder on CUDA | `GPUReranker._score_pairs()` |
| Reranker Factory | agent/retrieval/reranker/reranker_factory.py | Singleton factory; lazy build, warm start | `create_reranker()`, `init_reranker()` |
| Hardware Detector | agent/retrieval/reranker/hardware.py | Returns 'gpu'/'cpu' from env or torch | `detect_hardware()` |
| Cache | agent/retrieval/reranker/cache.py | LRU score cache; SHA-256 keyed; hit/miss counters | `cache_get()`, `cache_set()`, `cache_stats()` |
| Deduplicator | agent/retrieval/reranker/deduplicator.py | Pre-rerank dedup by snippet hash | `deduplicate_candidates()` |
| Symbol Query Detector | agent/retrieval/reranker/symbol_query_detector.py | Bypass gate for symbol/filename queries | `is_symbol_query()` |
| Preprocessor | agent/retrieval/reranker/preprocessor.py | Token truncation for rerank pairs | `prepare_rerank_pairs()` |

## Graph Index (repo_graph/)

| Component | File Location | Purpose | Key Entry Points |
|-----------|---------------|---------|------------------|
| Graph Storage | repo_graph/graph_storage.py | SQLite nodes/edges backend | `add_node()`, `add_edge()`, `get_symbol()`, `get_neighbors()` |
| Graph Query | repo_graph/graph_query.py | BFS expansion, dependency expansion, find_symbol | `find_symbol()`, `expand_neighbors()`, `expand_symbol_dependencies()`, `get_callers()`, `get_callees()`, `get_imports()`, `get_referenced_by()` |
| Graph Builder | repo_graph/graph_builder.py | Inserts symbols and edges from parsed data | `build_graph()` |
| Repo Map Builder | repo_graph/repo_map_builder.py | Generates repo_map.json from graph | `build_repo_map()`, `build_repo_map_from_storage()` |
| Change Detector | repo_graph/change_detector.py | Edit impact analysis; risk levels | `detect_change_impact()` |
| Repo Map Updater | repo_graph/repo_map_updater.py | Incremental repo_map update per file | `update_repo_map_for_file()` |

## Localization (agent/retrieval/localization/)

| Component | File Location | Purpose | Key Entry Points |
|-----------|---------------|---------|------------------|
| Localization Engine | agent/retrieval/localization/localization_engine.py | Orchestrates dependency + execution paths + ranking | `localize_issue()` |
| Dependency Traversal | agent/retrieval/localization/dependency_traversal.py | BFS over callers, callees, imports | `traverse_dependencies()` |
| Execution Path Analyzer | agent/retrieval/localization/execution_path_analyzer.py | Traces call paths from anchor | `build_execution_paths()` |
| Symbol Ranker | agent/retrieval/localization/symbol_ranker.py | Scores candidates by hop distance, path membership | `rank_localization_candidates()` |

## Reference Lookup and Call-Chain Context

| Component | File Location | Purpose | Key Entry Points |
|-----------|---------------|---------|------------------|
| Reference Tools | agent/tools/reference_tools.py | find_referencing_symbols, read_symbol_body | `find_referencing_symbols()`, `read_symbol_body()` |
| Context Builder (call-chain) | agent/retrieval/context_builder.py | build_call_chain_context from execution_path_analyzer | `build_call_chain_context()` |

## Execution Layer

| Component | File Location | Purpose | Key Entry Points |
|-----------|---------------|---------|------------------|
| Step Dispatcher | agent/execution/step_dispatcher.py | Central tool entry point; ToolGraph → Router → PolicyEngine → tool | `dispatch(step, state)` |
| Policy Engine | agent/execution/policy_engine.py | Per-action retry policies; query rewrite on SEARCH retry | `ExecutionPolicyEngine`, `classify_result()`, `validate_step_input()` |
| Tool Graph | agent/execution/tool_graph.py | Allowed tools per node; tool ordering | `get_allowed_tools()`, `get_preferred_tool()` |
| Tool Graph Router | agent/execution/tool_graph_router.py | Resolves which tool for action | `resolve_tool()` |
| Explain Gate | agent/execution/explain_gate.py | Ensures ranked context before EXPLAIN | `ensure_context_before_explain()` |

## Meta Layer (Trajectory, Critic, Retry)

| Component | File Location | Purpose | Key Entry Points |
|-----------|---------------|---------|------------------|
| Critic | agent/meta/critic.py | Failure diagnosis from trace | `diagnose()`, `Diagnosis` |
| Retry Planner | agent/meta/retry_planner.py | Maps diagnosis to retry strategy | `plan_retry()`, `RetryHints` |
| Trajectory Loop | agent/meta/trajectory_loop.py | attempt → evaluate → critique → plan_retry → retry | `TrajectoryLoop.run_with_retries()` |
| Trajectory Store | agent/meta/trajectory_store.py | Persists trajectory records to .agent_memory/ | save/load trajectory |
| Evaluator | agent/meta/evaluator.py | Task success/failure from step results | `evaluate()` |

## Failure Mining

| Component | File Location | Purpose | Key Entry Points |
|-----------|---------------|---------|------------------|
| Failure Extractor | agent/failure_mining/failure_extractor.py | Converts trajectories to FailureRecords; loop/hallucination detection | `extract_records()` |
| Failure Clusterer | agent/failure_mining/failure_clusterer.py | Multi-dimensional clustering | `cluster_all()`, `cluster_by_failure_type()` |
| Failure Judge | agent/failure_mining/failure_judge.py | LLM re-classification of unknown records | `label_failure()`, `relabel_unknown_records()` |
| Root Cause Report | agent/failure_mining/root_cause_report.py | Assembles failure analysis report | Report generation |
| Trajectory Loader | agent/failure_mining/trajectory_loader.py | Reads trajectory JSON from .agent_memory/ | Load trajectories |

## Configuration

| Component | File Location | Purpose | Key Entry Points |
|-----------|---------------|---------|------------------|
| Retrieval Config | config/retrieval_config.py | Retrieval, reranker, BM25, RRF, dedup, budget | All retrieval_* and RERANKER_* constants |
| Repo Graph Config | config/repo_graph_config.py | Symbol graph paths | SYMBOL_GRAPH_DIR, REPO_MAP_JSON, INDEX_SQLITE |
| Agent Config | config/agent_config.py | Agent loop limits | MAX_TASK_RUNTIME_SECONDS, MAX_REPLAN_ATTEMPTS, etc. |
| Config Validator | config/config_validator.py | Startup validation | `validate_all()` |

## Observability

| Component | File Location | Purpose | Key Entry Points |
|-----------|---------------|---------|------------------|
| Trace Logger | agent/observability/trace_logger.py | Records events and stage timings to .agent_memory/traces/ | `start_trace()`, `log_event()`, `log_stage()`, `finish_trace()` |
| UX Metrics | agent/observability/ux_metrics.py | Latency, success rates, context utilization | Metrics collection |

## Telemetry Fields (state.context["retrieval_metrics"])

| Field | Source | Meaning |
|-------|--------|---------|
| rerank_latency_ms | retrieval_pipeline | Reranker inference time (ms) |
| rerank_model | retrieval_pipeline | Model name (GPU or CPU) |
| rerank_device | retrieval_pipeline | gpu or cpu |
| rerank_dedup_removed | retrieval_pipeline | Candidates removed by pre-rerank dedup |
| rerank_cache_hits | reranker/cache | Score cache hits |
| rerank_cache_misses | reranker/cache | Score cache misses |
| rerank_tokens | retrieval_pipeline | Approximate tokens in rerank batch |
| rerank_skipped_reason | retrieval_pipeline | Why reranker was skipped (symbol_query, etc.) |
| rerank_position_changes | retrieval_pipeline | Position changes after rerank |
| rerank_avg_rank_shift | retrieval_pipeline | Average rank shift |
| rerank_top1_changed | retrieval_pipeline | Whether top-1 result changed |
| graph_nodes_expanded | symbol_expander (via graph_telemetry) | Nodes expanded in graph stage |
| graph_edges_traversed | symbol_expander | Edges traversed |
| graph_expansion_depth_used | symbol_expander | Depth used |
| graph_stage_skipped | retrieval_pipeline | True when index absent |
| dedupe_removed_count | retrieval_pipeline | Candidates removed by deduplicator |
| candidate_budget_applied | retrieval_pipeline | Candidates trimmed by MAX_RERANK_CANDIDATES |
