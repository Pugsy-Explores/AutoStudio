# Phase 17 — Retrieval Reranker Infrastructure

## Goal

Add a configurable cross-encoder reranker sub-package to the AutoStudio retrieval pipeline. The reranker plugs in after candidate assembly and before context pruning, auto-selecting between a GPU FP16 sentence-transformers model and a CPU INT8 ONNX model. Includes production-grade improvements for latency, robustness, and observability.

**Extensions (Retrieval System Upgrade):** BM25 lexical layer, Reciprocal Rank Fusion, reranker score threshold, RerankQueue for query batching, reranker impact telemetry, configurable score fusion, retrieval eval script.

## Architecture

```
hybrid_retrieve (BM25 + graph + vector + grep)
        ↓
reciprocal_rank_fusion (RRF merges → top 100)
        ↓
candidate assembly (anchors + symbols + localization)
        ↓
symbol_query_detector → bypass if symbol query
        ↓
adaptive gate → bypass if candidates < RERANK_MIN_CANDIDATES
        ↓
deduplicator (snippet hash)
        ↓
preprocessor (truncate + window, MAX_RERANK_PAIR_TOKENS)
        ↓
cache lookup (hash query+snippet)
        ↓
cross-encoder reranker (batched, top-K out)
        ↓
score fusion (0.8×reranker + 0.2×retriever)
        ↓
context_pruner → state.context
```

Fallback: on any failure or bypass condition → existing `context_ranker` (LLM-based).

## Implemented Components

### agent/retrieval/reranker/

- **hardware.py** — `detect_hardware()` → `"cpu"` | `"gpu"`; respects `RERANKER_DEVICE` override, probes `torch.cuda.is_available()`
- **cache.py** — LRU score cache keyed by SHA-256(query+snippet); `RERANK_CACHE_SIZE=2048`; thread-safe; tracks hits/misses for telemetry
- **preprocessor.py** — `prepare_rerank_pairs()` truncates snippets to `MAX_RERANK_SNIPPET_TOKENS` (256), enforces `MAX_RERANK_PAIR_TOKENS` (512)
- **symbol_query_detector.py** — `is_symbol_query()` returns `(bypass, reason)`; regex patterns for CamelCase, filenames, snake_case, keyword prefixes
- **deduplicator.py** — `deduplicate_candidates()` by snippet hash; preserves order
- **base_reranker.py** — Abstract `BaseReranker`; owns cache integration, adaptive gating, score threshold filtering; subclasses implement `_score_pairs()`; `rerank_batch()` for multi-query inference
- **gpu_reranker.py** — `sentence_transformers.CrossEncoder` FP16 on Volta+; batched via `RERANKER_BATCH_SIZE`
- **cpu_reranker.py** — ONNX INT8 `InferenceSession`; `AutoTokenizer` batching; sub-batches by `RERANKER_BATCH_SIZE`
- **reranker_factory.py** — `create_reranker()` singleton; `init_reranker()` warm-start (runs `rerank("warmup query", ["warmup snippet"])`); `_RERANKER_DISABLED` on failure
- **rerank_queue.py** — `RerankQueue` for batched reranking; `add()`, `flush()`, `clear()`; `RERANK_BATCH_WINDOW_MS` for coalescing

Score fusion uses configurable weights (`SCORE_FUSION_RERANKER_WEIGHT`, `SCORE_FUSION_RETRIEVER_WEIGHT`). Reranker output is filtered by `RERANK_SCORE_THRESHOLD`; at least `RERANK_MIN_RESULTS_AFTER_THRESHOLD` results are kept.

### agent/retrieval/ (extensions)

- **bm25_retriever.py** — BM25 index from symbol graph or repo_map; `build_bm25_index()`, `search_bm25()`; exact identifier queries
- **rank_fusion.py** — `reciprocal_rank_fusion()` with RRF (k=60, top_n=100); merges BM25, vector, graph, grep

### Config (config/retrieval_config.py)

- `RERANKER_ENABLED`, `RERANKER_DEVICE`, `RERANKER_TOP_K`, `RERANKER_BATCH_SIZE`
- `RERANKER_GPU_MODEL`, `RERANKER_CPU_MODEL`, `RERANKER_ALTERNATE_MODELS`
- `MAX_RERANK_SNIPPET_TOKENS`, `MAX_RERANK_PAIR_TOKENS`
- `RERANK_MIN_CANDIDATES`, `RERANK_CACHE_SIZE`
- `SCORE_FUSION_RERANKER_WEIGHT`, `SCORE_FUSION_RETRIEVER_WEIGHT`
- **Extensions:** `RERANK_SCORE_THRESHOLD`, `RERANK_MIN_RESULTS_AFTER_THRESHOLD`, `ENABLE_BM25_SEARCH`, `BM25_TOP_K`, `ENABLE_RRF_FUSION`, `RRF_TOP_N`, `RRF_K`, `RERANK_BATCH_WINDOW_MS`

### Pipeline Integration (agent/retrieval/retrieval_pipeline.py)

- Reranker gate: symbol bypass → adaptive gate → dedup → rerank → score fusion → prune
- Failure fallback to `context_ranker`; never crashes retrieval
- Telemetry: `rerank_latency_ms`, `rerank_model`, `rerank_device`, `candidates_in/out`, `rerank_dedup_removed`, `rerank_cache_hits/misses`, `rerank_tokens`, `rerank_batch_size`, `rerank_skipped_reason`
- **Impact telemetry:** `rerank_position_changes`, `rerank_avg_rank_shift`, `rerank_top1_changed` (tracks reranker effect on final ranking)

### Scripts and Docs

- **scripts/download_reranker.py** — Auto-detect hardware; download GPU (Qwen3-Reranker-0.6B) or CPU (ONNX INT8) to `models/reranker/`; `--device`, `--model` flags
- **scripts/run_retrieval_eval.py** — Eval on `tests/failure_mining_tasks.json`; recall@10/20, latency, candidate counts; `--limit N`
- **tests/test_reranker.py** — Hardware, cache, preprocessor, symbol detector, deduplicator, factory, adaptive gating, score fusion, warm-up, telemetry
- **tests/test_bm25_retriever.py**, **tests/test_rank_fusion.py**, **tests/test_reranker_threshold.py**, **tests/test_reranker_batching.py**, **tests/test_rerank_metrics.py**
- **Docs/RETRIEVAL_ARCHITECTURE.md** — Full pipeline, BM25, RRF, reranking layer, config reference, telemetry

## Usage

```bash
# Download model (auto-detect hardware)
python scripts/download_reranker.py

# Force CPU or GPU
python scripts/download_reranker.py --device cpu
python scripts/download_reranker.py --device gpu

# Alternate model
python scripts/download_reranker.py --model BAAI/bge-reranker-v2-gemma
```

```python
# Warm start at process startup (optional but recommended)
from agent.retrieval.reranker import init_reranker
init_reranker()
```

## Configuration

| Variable | Default | Purpose |
|----------|---------|---------|
| `RERANKER_ENABLED` | `true` | Master on/off |
| `RERANKER_DEVICE` | `auto` | `auto` \| `cpu` \| `gpu` |
| `RERANKER_TOP_K` | `10` | Reranker output size |
| `RERANKER_BATCH_SIZE` | `16` | Inference batch size |
| `RERANK_MIN_CANDIDATES` | `6` | Minimum candidates to trigger reranker |
| `MAX_RERANK_SNIPPET_TOKENS` | `256` | Per-snippet truncation |
| `MAX_RERANK_PAIR_TOKENS` | `512` | Hard cap query+snippet |
| `RERANK_CACHE_SIZE` | `2048` | LRU cache capacity |
| `SCORE_FUSION_RERANKER_WEIGHT` | `0.8` | Reranker weight in fusion |
| `SCORE_FUSION_RETRIEVER_WEIGHT` | `0.2` | Retriever weight in fusion |
| `RERANK_SCORE_THRESHOLD` | `0.0` | Filter low-relevance results below threshold |
| `RERANK_MIN_RESULTS_AFTER_THRESHOLD` | `3` | Minimum results kept after threshold filter |
| `ENABLE_BM25_SEARCH` | `true` | BM25 lexical retrieval layer |
| `BM25_TOP_K` | `30` | BM25 result cap |
| `ENABLE_RRF_FUSION` | `true` | Reciprocal Rank Fusion for merge |
| `RRF_TOP_N` | `100` | RRF merged result cap |
| `RRF_K` | `60` | RRF constant |
| `RERANK_BATCH_WINDOW_MS` | `50` | RerankQueue coalescing window |

## Benefits

- 10–30× faster than LLM reranking
- Better relevance ranking via cross-encoder
- Scalable for large repos
- Graceful fallback; retrieval pipeline never crashes
- **Extensions:** BM25 improves exact identifier recall; RRF avoids score-scale issues; threshold filtering drops low-relevance snippets; batched reranking reduces latency; impact telemetry supports tuning

## References

- [Docs/RETRIEVAL_ARCHITECTURE.md](../../Docs/RETRIEVAL_ARCHITECTURE.md) — Full retrieval pipeline and reranking layer
- [Docs/REPOSITORY_SYMBOL_GRAPH.md](../../Docs/REPOSITORY_SYMBOL_GRAPH.md) — Symbol graph and graph expansion
