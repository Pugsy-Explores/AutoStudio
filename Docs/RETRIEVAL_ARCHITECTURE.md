# Retrieval Architecture

This document describes the full AutoStudio retrieval pipeline, with emphasis on the hybrid retrieval layer (BM25, vector, graph, grep), Reciprocal Rank Fusion, and the cross-encoder reranking layer.

## Stabilized Pipeline (SEARCH_CANDIDATES → BUILD_CONTEXT → EXECUTOR)

```
SEARCH_CANDIDATES (candidate discovery only, < 1s)
    ├── BM25, vector, repo_map, grep
    ├── query expansion (unless symbol query)
    └── RRF merge → top 20 candidates
        │
        ▼
BUILD_CONTEXT (heavy operations, < 5s)
    ├── graph expansion
    ├── symbol body read
    ├── reranker
    ├── context pruning
    └── context builder
        │
        ▼
EXECUTOR (EDIT, EXPLAIN, etc.)
```

## Phase 5A — Explicit docs retrieval lane (`artifact_mode`)

Phase 5A adds an explicit retrieval lane selector field named `artifact_mode` on steps and tool adapters.

- **Allowed values**: `"code"` (default), `"docs"`.
- **Default**: when `artifact_mode` is missing, the system behaves exactly as code mode.
- **No auto-detection**: the lane is only selected when the step explicitly sets `artifact_mode="docs"`.

### Code lane (`artifact_mode="code"`)

Unchanged. Uses the existing hybrid retrieval pipeline (BM25/vector/graph/grep + RRF, then graph expansion, symbol reads, reference lookup, reranker, pruning).

### Docs lane (`artifact_mode="docs"`)

Deterministic, filesystem-driven docs retrieval:

- **Candidate discovery**: scan documentation artifacts under `project_root` (repo-root `README*`, `*.md`, `docs/**`, `Docs/**`; optionally `*.rst`, `*.txt`).
- **Exclusions**: `tests/**`, `test/**`, paths containing `test`, `node_modules/**`, `vendor/**`, `.venv/**`, `.git/**`, and obvious binary files.
- **Scoring**: explicit deterministic heuristics (repo-root README boost, docs-dir boost, query-token overlap on path + early content, examples penalty).
- **Context build**: read only top-ranked doc files (bounded), emit `state.context["ranked_context"]` entries with `{file, symbol:"", snippet, artifact_type:"doc"}` for EXPLAIN.

Docs lane explicitly **does not** use: graph expansion, symbol expansion, localization, reference traversal, BM25, code vector retrieval, code grep, or reranker.

## Phase 5B — Planner/replanner contract (`artifact_mode` propagation)

Phase 5B wires the planner → execution contract so that the planner and replanner can **explicitly** emit (and preserve across replans) the optional step field:

- `artifact_mode`: `"code"` (default when omitted) or `"docs"`

The retrieval lane remains explicit and is **not inferred** inside retrieval. When replanning a docs-lane failure, the replanner preserves `artifact_mode="docs"` on `SEARCH_CANDIDATES` / `BUILD_CONTEXT` / `EXPLAIN` steps unless it intentionally changes strategy.

### Phase 5B.1 guardrails (fallback + preservation)

- **Planner fallback shape**: when planner output is invalid, the fallback remains a single `SEARCH` step **unless** there is an explicit docs-lane lineage signal (either the parsed plan contained valid `artifact_mode="docs"` on docs-compatible steps, or `retry_context.previous_attempts` contains a prior docs-lane plan). In that explicit lineage case, the fallback is docs-shaped: `SEARCH_CANDIDATES → BUILD_CONTEXT → EXPLAIN`, all with `artifact_mode="docs"`.
- **Replanner preservation rule (narrow)**: docs-mode is preserved only when the failed step is explicitly docs-mode, or when the active plan being replanned is explicitly docs-lane **by structure** (all docs-compatible steps are explicitly `artifact_mode="docs"`). This avoids leaking docs mode into unrelated replans.

## Phase 6A — Single-lane per task (Option A)

Phase 6A freezes a **single dominant artifact lane per task/attempt**. The lane is explicit and immutable.

### Task-level lane lock: `dominant_artifact_mode`

Every production-capable execution entrypoint initializes:

- `state.context["dominant_artifact_mode"]`: `"code"` or `"docs"`
- `state.context["lane_violations"] = []`

**Selection rule (deterministic):**

- Dominant lane is `"docs"` **iff** the resolved plan is explicitly docs-lane **by structure** for docs-compatible actions.
- Otherwise dominant lane is `"code"`.

Once set, `dominant_artifact_mode` is the **source of truth** for lane enforcement. Per-step `state.context["artifact_mode"]` may still reflect the current step, but it must never override the dominant lane lock.

### Docs-compatible actions

Docs-compatible actions are:

- `SEARCH_CANDIDATES`
- `BUILD_CONTEXT`
- `EXPLAIN`

### Dominant docs lane contract

When `dominant_artifact_mode="docs"`:

- **Allowed actions**: `SEARCH_CANDIDATES`, `BUILD_CONTEXT`, `EXPLAIN`
- **Forbidden actions**: `SEARCH`, `EDIT` (and any other non-docs-compatible action)
- **Explicitness requirement**: every docs-compatible step executed must explicitly set `artifact_mode="docs"` (no silent defaulting)

### Dominant code lane contract

When `dominant_artifact_mode="code"`:

- Code behavior remains default.
- **Forbidden**: any step with `artifact_mode="docs"`

### Enforcement points (frozen behavior)

- **Planner-time**: plan validation rejects mixed-lane plans (docs steps mixed with `SEARCH`/`EDIT`, or docs-compatible steps missing explicit `artifact_mode="docs"` when docs lane is indicated).
- **Replanner-time**: replans must remain in the dominant lane; lane-violating replans are rejected and replaced with a lane-consistent fallback.
- **Dispatcher runtime gate**: lane violations return a deterministic `lane_violation` error and **FATAL_FAILURE**.
- **Deterministic success path**: goal evaluation refuses success if any lane violations occurred (even if EXPLAIN otherwise succeeds).
- **Observability**: traces record `dominant_artifact_mode` and per-step lane fields (`dominant_artifact_mode`, `step_artifact_mode`) in step execution events.

### Production-capable entrypoints that initialize the lane lock

- Deterministic runner path: `run_controller` → `run_attempt_loop` → `run_deterministic`
- Orchestrator agent loop (deprecated): `agent/orchestrator/agent_loop.py::run_agent`
- Autonomous mode: `agent/autonomous/agent_loop.py::run_autonomous`
- Multi-agent workspace state creation: `agent/roles/workspace.py::AgentWorkspace.from_goal`

See also:
- [CODING_AGENT_ARCHITECTURE_GUIDE.md](CODING_AGENT_ARCHITECTURE_GUIDE.md) — end-to-end agent architecture
- [REPOSITORY_SYMBOL_GRAPH.md](REPOSITORY_SYMBOL_GRAPH.md) — symbol graph and graph expansion

---

## Hybrid Retrieval Pipeline

```
User Query
    │
    ▼
query_rewriter          (LLM or heuristic rewrite)
    │
    ▼
search_pipeline.hybrid_retrieve
    ├── bm25_retriever    (BM25 lexical: symbol names, docstrings, file paths)
    ├── graph_retriever   (SQLite symbol graph, depth=2)
    ├── vector_retriever  (ChromaDB + all-MiniLM-L6-v2)
    └── grep/regex search
    │
    ▼
reciprocal_rank_fusion  (RRF merges BM25, vector, graph, grep → top 100)
    │
    ▼
retrieval_pipeline.run_retrieval_pipeline
    ├── anchor_detector
    ├── localization_engine        [ENABLE_LOCALIZATION_ENGINE]
    ├── graph_stage_skipped check  (skip symbol_expander when index absent)
    ├── symbol_expander            (expand_symbol_dependencies: calls, imports, references)
    ├── retrieval_expander         (read_symbol_body / read_file)
    ├── find_referencing_symbols   (callers, callees, imports, referenced_by; cap 10 each)
    ├── context_builder            (build_call_chain_context when project_root + symbols)
    ├── deduplicate_candidates     (unconditional; SHA-256 snippet key)
    ├── candidate_budget           (slice to MAX_RERANK_CANDIDATES before reranker)
    │
    ├── ── cross-encoder reranker ──────────────────────────────┐
    │   symbol_query_detector  → bypass if symbol query         │
    │   adaptive gate          → bypass if < RERANK_MIN_CANDIDATES
    │   deduplicator           → remove duplicate snippets      │
    │   preprocessor           → truncate to token limits       │
    │   cache                  → skip inference on cache hits   │
    │   RerankQueue            → coalesce queries for batching  │
    │   GPUReranker / CPUReranker → batched inference           │
    │   score_fusion           → 0.8×reranker + 0.2×retriever  │
    │   ─────────────────────────────────────────────────────── │
    │   fallback → context_ranker (LLM-based)                   │
    │                                                           ◄┘
    ├── context_pruner            (max snippets + char budget)
    └── context_compressor        [repo_summary present]
    │
    ▼
state.context["ranked_context"]
    │
    ▼
LLM reasoning
```

---

## 1. BM25 Lexical Retrieval

`agent/retrieval/bm25_retriever.py` indexes symbol names, docstrings, file paths, and signatures from the symbol graph (or repo_map fallback). BM25 is critical for exact identifier queries (e.g. `StepExecutor`, `route_instruction`). Toggle via `ENABLE_BM25_SEARCH`; top-k via `BM25_TOP_K` (default 30).

BM25 scores from `rank_bm25` can be zero or negative (e.g. when IDF is low). Results are returned by rank order (top-k) regardless of raw score. Tests use `_reset_for_testing()` to isolate module state.

---

## 2. Reciprocal Rank Fusion (RRF)

`agent/retrieval/rank_fusion.py` merges BM25, vector, graph, and grep results using RRF to avoid score-scale problems. RRF score: `sum over lists of 1/(k + rank)`. Config: `ENABLE_RRF_FUSION`, `RRF_TOP_N` (100), `RRF_K` (60).

---

## 2.5 Graph Dependency Expansion and Reference Lookup

After anchor detection, the pipeline uses the symbol graph for dependency-aware expansion and reference lookup.

### Graph query helpers (`repo_graph/graph_query.py`)

| Function | Purpose |
|---|---|
| `get_callers(symbol_id, storage)` | Nodes that call this symbol (incoming calls) |
| `get_callees(symbol_id, storage)` | Nodes this symbol calls (outgoing calls) |
| `get_imports(symbol_id, storage)` | Nodes this symbol imports |
| `get_referenced_by(symbol_id, storage)` | Nodes that reference this symbol |

### expand_symbol_dependencies

BFS expansion along dependency edges (calls, imports, references). Cycle-safe; respects `RETRIEVAL_GRAPH_EXPANSION_DEPTH`, `RETRIEVAL_GRAPH_MAX_NODES`, and `RETRIEVAL_MAX_SYMBOL_EXPANSIONS`. Returns `(nodes, telemetry)` with `graph_nodes_expanded`, `graph_edges_traversed`, `graph_expansion_depth_used`.

### find_referencing_symbols (`agent/tools/reference_tools.py`)

Returns structured dict `{callers, callees, imports, referenced_by}`; each list capped at 10. Uses GraphStorage when `.symbol_graph/index.sqlite` exists; falls back to empty dict otherwise.

### build_call_chain_context (`agent/retrieval/context_builder.py`)

Formats execution paths as `symbol()\n  calls callee1()\n  calls callee2()`. Injected into `build_context_from_symbols` when `project_root` and symbols are present.

### Graph index fallback

When `.symbol_graph/index.sqlite` is absent, `graph_stage_skipped=True` is set in telemetry; `symbol_expander` is skipped; pipeline continues with grep/vector results only.

---

## 3. Vector Retrieval Stage

`agent/retrieval/vector_retriever.py` uses ChromaDB with the `all-MiniLM-L6-v2` sentence-transformers model for dense semantic search. Results are merged with BM25, graph, and grep by `search_pipeline.hybrid_retrieve` (parallel execution via `ThreadPoolExecutor`), then fused via RRF when `ENABLE_RRF_FUSION` is on. Deduplication by `(file, symbol, line)`; capped at `MAX_SEARCH_RESULTS` (default 20).

Each candidate produced by hybrid retrieval carries a `retriever_score` field (normalized to `[0, 1]`) that the reranker uses for score fusion.

---

## 4. Cross-Encoder Reranking Layer

The reranker lives in `agent/retrieval/reranker/` and is inserted into the pipeline after candidate assembly, before `context_pruner`.

**Startup:** When `RERANKER_STARTUP=1` (default), the reranker is auto-initialized at service startup before any other work. Set `RERANKER_STARTUP=0` to skip; the reranker will lazy-load on first retrieval.

### 4.1 When the reranker runs

All of the following must be true:

| Condition | Config / code |
|---|---|
| `RERANKER_ENABLED=true` | `config/retrieval_config.py` |
| Reranker loaded without error | `reranker_factory._RERANKER_DISABLED == False` |
| Query is not a symbol query | `symbol_query_detector.is_symbol_query()` returns `False` |
| Candidate count >= threshold | `len(candidates) >= RERANK_MIN_CANDIDATES` (default 6) |

When any condition fails the pipeline falls through to retriever-score ordering (no LLM fallback). The `rerank_skipped_reason` telemetry field records the reason.

### 4.2 Pipeline steps

Deduplication runs unconditionally before the reranker gate (see hybrid pipeline diagram). Candidates passed to the reranker are already deduped and budgeted to `MAX_RERANK_CANDIDATES`. When the reranker runs:

```
candidates (pre-deduped, pre-budgeted)
    │
    ▼
preprocessor.prepare_rerank_pairs()
    Truncates each snippet to MAX_RERANK_SNIPPET_TOKENS (default 256).
    Enforces MAX_RERANK_PAIR_TOKENS (default 512) across query + snippet.
    When snippet alone exceeds the pair limit, snippet is truncated to fit.
    │
    ▼
cache lookup  (per pair, keyed by SHA-256(query + snippet))
    Cache hits skip inference entirely.
    │
    ▼
BaseReranker._score_pairs()  — batched inference (RERANKER_BATCH_SIZE=16)
    GPU path: CrossEncoder.predict() with FP16 on Volta+ GPUs
    CPU path: ONNX INT8 session.run() with AutoTokenizer batching
    │
    ▼
cache population (new scores written back)
    │
    ▼
score threshold filter  (RERANK_SCORE_THRESHOLD; keep ≥ RERANK_MIN_RESULTS_AFTER_THRESHOLD)
    │
    ▼
score fusion
    final_score = reranker_score × SCORE_FUSION_RERANKER_WEIGHT (0.8)
                + retriever_score × SCORE_FUSION_RETRIEVER_WEIGHT (0.2)
    Sort descending; slice to RERANKER_TOP_K (default 10).
```

### 4.3 Model registry

| Model | Use case | Format |
|---|---|---|
| `Qwen/Qwen3-Reranker-0.6B` | GPU default | PyTorch FP16 |
| `Qwen/Qwen3-Reranker-0.6B-ONNX` | CPU default | ONNX INT8 |
| `BAAI/bge-reranker-v2-gemma` | GPU alternate | PyTorch |
| `jinaai/jina-reranker-v3` | GPU alternate | PyTorch |

Download the correct model with:

```bash
python scripts/download_reranker.py                        # auto-detect
python scripts/download_reranker.py --device cpu           # force CPU model
python scripts/download_reranker.py --model BAAI/bge-reranker-v2-gemma
```

### 4.4 Hardware auto-selection

`hardware.detect_hardware()` returns `"gpu"` or `"cpu"`:

1. If `RERANKER_DEVICE` is set to `"cpu"` or `"gpu"`, use that value directly.
2. Otherwise probe `torch.cuda.is_available()`.
3. Fall back to `"cpu"` when torch is absent.

### 4.5 Warm start

Call `init_reranker()` once at process startup (e.g. from the agent controller or worker entrypoint). This builds the singleton and runs a dummy inference pass to absorb CUDA kernel compilation, model graph creation, and memory allocation before the first real query.

```python
from agent.retrieval.reranker import init_reranker
init_reranker()  # idempotent; safe to call multiple times
```

### 4.6 Symbol query bypass

`symbol_query_detector.is_symbol_query(query)` returns `(True, reason)` for queries that are better served by lexical + graph retrieval:

- CamelCase identifiers (`RetrievalPipeline`)
- File names (`retrieval_pipeline.py`)
- `snake_case_symbol` bare words
- Python/JS keyword prefixes (`def run`, `class Foo`, `import X`)

### 4.7 RerankQueue (query batching)

`agent/retrieval/reranker/rerank_queue.py` provides `RerankQueue` for coalescing multiple rerank requests into batched inference. Use `add(query, snippets)` to enqueue, `flush()` to run batched inference, and `clear()` to reset. The coalescing window is `RERANK_BATCH_WINDOW_MS` (default 50 ms).

### 4.8 Failure fallback

Any exception during model load, warmup, or `create_reranker()`:

1. Logs a warning via the standard logger.
2. Sets `_RERANKER_DISABLED = True` in the factory (permanent for the process lifetime).
3. `create_reranker()` returns `None`; pipeline falls through to retriever-score ordering.
4. Records `rerank_skipped_reason = "inference_error:<ExceptionType>"` in telemetry.

The retrieval pipeline **never crashes** due to reranker failures.

### 4.9 Retrieval Daemon (reranker + embedding)

`scripts/retrieval_daemon.py` runs both the reranker and the embedding model (all-MiniLM-L6-v2) as a standalone HTTP service. When `RERANKER_USE_DAEMON=1` and `EMBEDDING_USE_DAEMON=1` (defaults), the agent uses the daemon instead of loading models in-process, avoiding cold-start latency.

| Endpoint | Body | Response |
|----------|------|----------|
| `POST /rerank` | `{"query": "...", "docs": ["snippet1", ...]}` | `{"results": [(snippet, score), ...]}` |
| `POST /embed` | `{"texts": ["text1", "text2", ...]}` | `{"embeddings": [[...], [...]]}` |
| `GET /health` | — | `{"reranker_loaded": bool, "embedding_loaded": bool}` |

Start the daemon before agent sessions:

```bash
python scripts/retrieval_daemon.py              # foreground
python scripts/retrieval_daemon.py --daemon     # background
python scripts/retrieval_daemon.py --stop       # stop daemon
```

Config: `RETRIEVAL_DAEMON_PORT` (default 9004), `RERANKER_USE_DAEMON`, `EMBEDDING_USE_DAEMON`.

---

## 5. Context Pruning

`context_pruner.prune_context(candidates, max_snippets, max_chars)` enforces the final budget:

- `MAX_CONTEXT_SNIPPETS` (default 6) — maximum number of snippets
- `DEFAULT_MAX_CHARS` (default 8000) — maximum total character count

Deduplicates by `(file, symbol)` pair. Iterates ranked candidates in order and stops when either budget is exceeded.

---

## 6. Configuration Reference

All values are overridable via environment variables.

| Variable | Default | Purpose |
|---|---|---|
| `RERANKER_ENABLED` | `true` | Master on/off switch |
| `RERANKER_USE_DAEMON` | `true` | Prefer retrieval daemon for reranker when reachable |
| `EMBEDDING_USE_DAEMON` | `true` | Prefer retrieval daemon /embed for vector search when reachable |
| `RETRIEVAL_DAEMON_PORT` | `9004` | Retrieval daemon HTTP port (reranker + embedding) |
| `RERANKER_DEVICE` | `auto` | `auto` \| `cpu` \| `gpu` |
| `RERANKER_GPU_MODEL` | `Qwen/Qwen3-Reranker-0.6B` | GPU model HuggingFace ID |
| `RERANKER_CPU_MODEL` | from `models_config.json` reranker.cpu_model | CPU ONNX model path; env overrides |
| `RERANKER_TOP_K` | `10` | Reranker output size |
| `RERANKER_BATCH_SIZE` | `16` | Inference batch size |
| `RERANK_MIN_CANDIDATES` | `6` | Minimum candidates to trigger reranker |
| `MAX_RERANK_SNIPPET_TOKENS` | `256` | Per-snippet token truncation limit |
| `MAX_RERANK_PAIR_TOKENS` | `512` | Hard cap on query + snippet tokens |
| `RERANK_CACHE_SIZE` | `2048` | LRU score cache capacity |
| `SCORE_FUSION_RERANKER_WEIGHT` | `0.8` | Reranker score weight in fusion |
| `SCORE_FUSION_RETRIEVER_WEIGHT` | `0.2` | Retriever score weight in fusion |
| `RERANK_FUSION_WEIGHT` | `0.8` | Reranker weight (alias) |
| `RETRIEVER_FUSION_WEIGHT` | `0.2` | Retriever weight (alias) |
| `RERANK_SCORE_THRESHOLD` | `0.15` | Discard results below this score |
| `RERANK_MIN_RESULTS_AFTER_THRESHOLD` | `3` | Fallback: keep top_k if fewer pass |
| `ENABLE_BM25_SEARCH` | `true` | BM25 lexical retrieval toggle |
| `BM25_TOP_K` | `30` | BM25 result count |
| `ENABLE_RRF_FUSION` | `true` | Reciprocal Rank Fusion toggle |
| `RRF_TOP_N` | `100` | RRF merged result cap |
| `RRF_K` | `60` | RRF constant |
| `RERANK_BATCH_WINDOW_MS` | `50` | RerankQueue coalescing window |
| `MAX_RERANK_CANDIDATES` | `50` | Cap candidates before reranker (candidate budget) |
| `RETRIEVAL_GRAPH_EXPANSION_DEPTH` | `2` | BFS depth for expand_symbol_dependencies |
| `RETRIEVAL_GRAPH_MAX_NODES` | `20` | Max nodes per symbol expansion |
| `RETRIEVAL_MAX_SYMBOL_EXPANSIONS` | `8` | Per-symbol expansion cap (safety limit) |
| `MAX_CONTEXT_SNIPPETS` | `6` | Final snippet count after pruning |
| `DEFAULT_MAX_CHARS` | `8000` | Final char budget after pruning |
| `ENABLE_CONTEXT_RANKING` | `true` | (Unused by retrieval pipeline; kept for compatibility) |

---

## 7. Telemetry

The reranker emits metrics into `state.context["retrieval_metrics"]` after every pipeline run:

| Field | Type | Description |
|---|---|---|
| `ranking_method` | str | `"reranker"` = cross-encoder (Qwen3-Reranker); `"retriever_score"` = retriever-score ordering when reranker skipped |
| `rerank_latency_ms` | int | End-to-end reranker wall time |
| `rerank_model` | str | Model ID or path used |
| `rerank_device` | str | `"gpu"`, `"cpu"`, or `"none"` |
| `candidates_in` | int | Candidate count before deduplication |
| `candidates_out` | int | Final ranked snippet count |
| `rerank_dedup_removed` | int | Duplicates removed before reranking |
| `rerank_cache_hits` | int | Cache hits across all pairs |
| `rerank_cache_misses` | int | Cache misses (inference required) |
| `rerank_tokens` | int | Total tokens processed |
| `rerank_batch_size` | int | Batch size used |
| `rerank_skipped_reason` | str \| None | Reason reranker was skipped, or `None` |
| `rerank_position_changes` | int | Number of docs that changed rank |
| `rerank_avg_rank_shift` | float | Average absolute rank change |
| `rerank_top1_changed` | int | 1 if top-1 doc changed, else 0 |
| `dedupe_removed_count` | int | Duplicates removed (unconditional stage) |
| `candidate_count` | int | Candidate count after deduplication |
| `candidate_budget_applied` | int | Candidates trimmed by MAX_RERANK_CANDIDATES |
| `graph_nodes_expanded` | int | Nodes added by expand_symbol_dependencies |
| `graph_edges_traversed` | int | Edges traversed during BFS expansion |
| `graph_expansion_depth_used` | int | Actual max depth reached in BFS |
| `graph_stage_skipped` | bool | True when .symbol_graph/index.sqlite absent |

---

## 8. Retrieval Evaluation

`scripts/run_retrieval_eval.py` evaluates retrieval on `tests/failure_mining_tasks.json`:

```bash
python scripts/run_retrieval_eval.py --limit 5
```

Reports recall@10, recall@20, latency, and candidate counts. Requires `rank_bm25` for BM25 tests (`pip install rank_bm25`).

---

## 9. Troubleshooting: Reranker Missing / LLM Fallback

If you see `Load model ... failed` and the pipeline falls back to the LLM ranker (100× slower):

1. **Download the reranker model first:**
   ```bash
   python scripts/download_reranker.py --device cpu
   ```
   This fetches `model.onnx` and tokenizer files to `models/reranker/`.

2. **Ensure the path matches:** The default `RERANKER_CPU_MODEL` is `models/reranker/model.onnx` (matches the download output). If you use a custom path, set `RERANKER_CPU_MODEL` to the correct location.

3. **Working directory:** The reranker resolves `models/reranker/` relative to the project root (inferred from the agent package location when `SERENA_PROJECT_DIR` is unset). If you run from a different cwd, it should still find the model. Override with `SERENA_PROJECT_DIR` if needed.

4. **Override via env:** `export RERANKER_CPU_MODEL=/absolute/path/to/model.onnx` if needed.
