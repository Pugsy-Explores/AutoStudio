# AutoStudio Observability

Reference for trace logging, telemetry fields, and metrics. All observability data supports debugging, performance analysis, and CI guardrails.

See also:
- [AGENT_CONTROLLER.md](AGENT_CONTROLLER.md) — trace events in full pipeline
- [CONFIGURATION.md](CONFIGURATION.md) — observability_config

---

## Trace Logging

Traces are written to `.agent_memory/traces/<trace_id>.json`. Each trace includes plan, tool calls, patch results, errors, and task_complete summary.

**Entry points:** `agent/observability/trace_logger.py`

| Function | Purpose |
|----------|---------|
| `start_trace()` | Begin a new trace |
| `log_event(trace_id, event_type, payload)` | Record an event |
| `log_stage(trace_id, stage_name, latency_ms, step_id, summary)` | Record a stage with timing |
| `finish_trace()` | Finalize trace |

**Stages:** `context_grounder`, `intent_router`, `planner`, `query_rewrite`, `retrieval`, `symbol_expansion`, `context_ranker`, `context_pruner`, `reasoning`, `validation`

---

## Retrieval Metrics

Stored in `state.context["retrieval_metrics"]`. Emitted by `run_retrieval_pipeline` and consumed by trace logger and evaluation scripts.

### Reranker Telemetry

| Field | Type | Meaning |
|-------|------|---------|
| rerank_latency_ms | int | Reranker inference time in milliseconds |
| rerank_model | str | Model name (RERANKER_GPU_MODEL or RERANKER_CPU_MODEL) |
| rerank_device | str | `gpu` or `cpu` |
| rerank_cache_hits | int | Score cache hits (skipped inference) |
| rerank_cache_misses | int | Score cache misses (inference performed) |
| rerank_dedup_removed | int | Candidates removed by pre-rerank deduplicator |
| rerank_tokens | int | Approximate tokens in rerank batch |
| rerank_batch_size | int | Batch size used for inference |
| rerank_skipped_reason | str \| null | Why reranker was skipped (e.g. `symbol_query`, `min_candidates`) |
| rerank_position_changes | int | Number of candidates whose rank changed after rerank |
| rerank_avg_rank_shift | float | Average absolute rank shift |
| rerank_top1_changed | int | 1 if top-1 result changed; 0 otherwise |
| candidates_in | int | Candidates before reranker |
| candidates_out | int | Candidates after reranker |

### Graph Telemetry

| Field | Type | Meaning |
|-------|------|---------|
| graph_nodes_expanded | int | Nodes expanded in graph expansion stage |
| graph_edges_traversed | int | Edges traversed during expansion |
| graph_expansion_depth_used | int | BFS depth actually used |
| graph_stage_skipped | bool | True when `.symbol_graph/index.sqlite` absent; graph stage skipped |

### Deduplication and Budget

| Field | Type | Meaning |
|-------|------|---------|
| dedupe_removed_count | int | Candidates removed by deduplicator (snippet hash) |
| candidate_budget_applied | int | Candidates trimmed by MAX_RERANK_CANDIDATES before reranker |
| candidate_count | int | Candidate count after dedup, before budget |

---

## UX Metrics

Per-task metrics written by `run_controller` to `reports/ux_metrics.json`:

| Field | Meaning |
|-------|---------|
| interaction_latency | Latency of user interaction |
| steps_per_task | Number of steps executed |
| tool_calls | Tool invocation count |
| patch_success | Whether patch was applied successfully |

---

## Phase 16 Failure Mining Metrics

From `run_failure_mining.py`; output in `reports/failure_stats.json`:

| Metric | Meaning |
|--------|---------|
| avg_steps_success | Average steps when task succeeded |
| avg_steps_failure | Average steps when task failed |
| loop_failure_rate | Fraction of failures due to loop |
| retrieval_miss_rate | Fraction of failures due to retrieval miss |
| patch_error_rate | Fraction of failures due to patch error |

**CI guardrails:** `retrieval_miss_rate < 40%`, `patch_error_rate < 25%` (when `reports/failure_stats.json` exists).
