# Cross-Encoder Reranker Subsystem

Reranks retrieval candidates using a cross-encoder model (Qwen3-Reranker-0.6B). Supports GPU (sentence-transformers) and CPU (ONNX INT8). Includes cache, deduplication, symbol-query bypass, and score fusion.

## Purpose

Improves retrieval precision by re-scoring candidates with a cross-encoder before context pruning. Runs after deduplication and candidate budget; falls back to LLM ranker when disabled or when query is a symbol/filename query.

## Architecture

| Module | Purpose |
|--------|---------|
| base_reranker | Abstract base; cache integration, adaptive gating, preprocessing |
| gpu_reranker | Sentence-transformers CrossEncoder on CUDA |
| cpu_reranker | ONNX INT8 inference (Qwen3-Reranker-0.6B) |
| reranker_factory | Singleton factory; lazy build; warm start |
| cache | LRU score cache (SHA-256 keyed); hit/miss counters |
| deduplicator | Pre-rerank dedup by snippet hash |
| symbol_query_detector | Bypass gate for CamelCase/snake_case/filename queries |
| preprocessor | Token truncation (MAX_RERANK_SNIPPET_TOKENS, MAX_RERANK_PAIR_TOKENS) |
| hardware | Detects GPU/CPU from env or torch |

## Key Classes

- `BaseReranker` — abstract; `rerank(query, docs)`, `_score_pairs()` (implemented by GPU/CPU)
- `create_reranker()` — factory; returns GPU or CPU reranker
- `init_reranker()` — warm start (load model before first query)
- `deduplicate_candidates()` — removes duplicate snippets
- `is_symbol_query()` — returns (bypass, reason)

## Data Flow

1. Candidates (already deduped, budgeted) enter reranker gate
2. Gate checks: RERANKER_ENABLED, model loaded, not symbol query, len >= RERANK_MIN_CANDIDATES
3. Preprocessor truncates snippets
4. Cache lookup per (query, snippet); cache hits skip inference
5. Batched inference (GPUReranker or CPUReranker)
6. Score fusion: 0.8×reranker + 0.2×retriever
7. Top-K selection; telemetry emitted

## Configuration

See [Docs/CONFIGURATION.md](../../Docs/CONFIGURATION.md) — retrieval_config: RERANKER_ENABLED, RERANKER_DEVICE, RERANKER_GPU_MODEL, RERANKER_CPU_MODEL, RERANKER_TOP_K, RERANK_CACHE_SIZE, etc.

## See Also

- [Docs/RETRIEVAL_ARCHITECTURE.md](../../Docs/RETRIEVAL_ARCHITECTURE.md) — reranker pipeline (sections 4.1–4.8)
- [Docs/OBSERVABILITY.md](../../Docs/OBSERVABILITY.md) — rerank telemetry fields
