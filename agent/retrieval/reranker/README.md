# Cross-Encoder Reranker Subsystem

Reranks retrieval candidates using a cross-encoder model (Qwen3-Reranker-0.6B). Supports INT8 on both CPU and GPU (ONNX); FP16 on GPU when RERANKER_USE_INT8=0. Includes cache, deduplication, symbol-query bypass, and score fusion.

## Purpose

Improves retrieval precision by re-scoring candidates with a cross-encoder before context pruning. Runs after deduplication and candidate budget; falls back to LLM ranker when disabled or when query is a symbol/filename query.

## Architecture

| Module | Purpose |
|--------|---------|
| base_reranker | Abstract base; cache integration, adaptive gating, preprocessing |
| gpu_reranker | Sentence-transformers CrossEncoder on CUDA (FP16; when RERANKER_USE_INT8=0) |
| onnx_gpu_reranker | ONNX INT8 on CUDA (default when GPU + RERANKER_USE_INT8=1) |
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

See [Docs/CONFIGURATION.md](../../Docs/CONFIGURATION.md) — retrieval_config: RERANKER_ENABLED, RERANKER_STARTUP (default ON), RERANKER_DEVICE, RERANKER_USE_INT8, RERANKER_GPU_MODEL, RERANKER_CPU_MODEL, RERANKER_TOP_K, RERANK_CACHE_SIZE, etc.

- **RERANKER_USE_INT8** (default 1): Use ONNX INT8 for both CPU and GPU. When 0, GPU uses sentence-transformers FP16.
- **RERANKER_STARTUP** (default 1): Auto-init reranker at service startup. Set to 0 to skip; reranker will lazy-load on first retrieval.

## Retrieval Daemon

Run the unified retrieval daemon (reranker + embedding) as a standalone HTTP service to avoid cold-start latency. The agent uses it when `RERANKER_USE_DAEMON=1` and `EMBEDDING_USE_DAEMON=1` (defaults).

```bash
python scripts/retrieval_daemon.py              # foreground
python scripts/retrieval_daemon.py --daemon     # background
python scripts/retrieval_daemon.py --stop       # stop daemon
```

Requires: `pip install fastapi uvicorn sentence-transformers`. Endpoints:

| Endpoint | Body | Response |
|----------|------|----------|
| `POST /rerank` | `{"query": "...", "docs": ["snippet1", ...]}` | `{"results": [(snippet, score), ...]}` |
| `POST /embed` | `{"texts": ["text1", "text2", ...]}` | `{"embeddings": [[...], [...]]}` |
| `GET /health` | — | `{"reranker_loaded": bool, "embedding_loaded": bool}` |

## See Also

- [Docs/RETRIEVAL_ARCHITECTURE.md](../../Docs/RETRIEVAL_ARCHITECTURE.md) — reranker pipeline (sections 4.1–4.8)
- [Docs/OBSERVABILITY.md](../../Docs/OBSERVABILITY.md) — rerank telemetry fields
