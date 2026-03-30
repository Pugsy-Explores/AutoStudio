# Retrieval Layer Migration Plan (Controlled Extraction)

**Scope:** AutoStudio (`agent/retrieval/`, `agent/tools/serena_adapter.py`, `agent/execution/step_dispatcher.py`, `repo_graph/`).  
**Goal:** Replace entangled orchestration with a **minimal, contract-driven** pipeline: four primitives → RRF → (optional single-authority rerank) → deterministic prune → output, **without** rewriting graph/BM25/vector/Serena cores.

**Note:** Execution order for the four retrievers is **parallel** (fan-in to RRF).

---

## Critical fixes (must apply before implementation)

### Issue 1 — Graph retrieval is not a single “primitive”

**Problem:** `graph_retriever(query) → expanded context` mixes **query interpretation** (NL → symbol candidates), **lookup**, and **expansion**. That makes graph a **hidden planner**: it can override query intent, inject bias, and break consistency with BM25/vector/Serena.

**Rule:** **Graph retrieval must NOT interpret query** inside the default pipeline.

**Split (mandatory):**

| Stage | Responsibility | Default in v2 pipeline? |
|-------|----------------|-------------------------|
| **`graph_lookup(query)`** | Deterministic lookup only: map `query` to **symbols / nodes** via `repo_graph` APIs (e.g. exact identifier match, or a **single** documented matching rule—no NL filler stripping, no multi-candidate “best guess” inside lookup). Output: **nodes/symbols only**, ordered list for RRF. | **Yes** |
| **`graph_expand(symbols | node_ids)`** | Optional neighbor/context expansion (existing `expand_neighbors` etc.). | **No** — separate stage, **not** part of default merge; only if an explicit planner step requests expansion. |

**Implementation note:** Refactor away from monolithic `retrieve_symbol_context` as the v2 graph source; implement **`graph_lookup`** as thin calls to `find_symbol` / storage (policy for “what string to pass” may live **outside** graph, e.g. same raw `query` string as other retrievers, **without** NL extraction inside graph).

---

### Issue 2 — Reranker policy (single authority)

**Problem:** “Rerank OR fusion” invites **multiple ranking authorities** (RRF + weighted fusion with retriever scores) and regressions.

**Mandatory policy — pick exactly one path:**

| Option | Pipeline | Status |
|--------|----------|--------|
| **A (recommended for v1)** | `RRF → final ranking` — **no** cross-encoder | **Default** — ship this first |
| **B** | `RRF → cross-encoder rerank → final ranking` — order **only** from reranker scores | **Optional later**, feature-flagged |

**Forbidden:**

- `RRF + reranker + weighted fusion` (e.g. `RERANK_FUSION_WEIGHT` * rerank + `RETRIEVER_FUSION_WEIGHT` * RRF) as part of v2.
- Any stage that blends RRF order with rerank scores.

If Option B is enabled: **replace** ordering with rerank output only (single authority after RRF merge step—not a blend).

---

### Issue 3 — Prune stage (deterministic)

**Problem:** “Truncate only” is under-specified and risks non-deterministic outputs.

**Specify explicitly:**

| Concern | Rule |
|---------|------|
| **Dedup key** | `(path_normalized, symbol_normalized, snippet_hash)` where `snippet_hash = SHA256(normalized_snippet)` (normalize whitespace for hash input). First occurrence **wins** (keeps RRF order). |
| **Ordering** | **Strictly preserve** post-RRF order (or post-rerank order if Option B). **No** `_KIND_RANK` or kind-based reordering in prune. |
| **Tie-break (only if needed)** | When two rows are indistinguishable for dedup (should be rare): lexical sort by **`path`** ascending as **secondary** stable key after primary order. |
| **Guarantee** | Same input list + same budgets → **identical** output sequence. |

Prune applies **after** path validation (file exists, under project root) if validation is part of this stage or immediately before.

---

## Medium fixes (should apply)

### Serena fallback

**Problem:** “No fallback” makes the system brittle when MCP is down.

**Fix:** **Explicit fallback layer**, not implicit `rg` inside `search_code`:

- **Layer 1:** Serena MCP (primary for `serena` source).
- **Layer 2 (optional, config):** e.g. `serena_fallback_rg` module with explicit glob (`SERENA_RG_FALLBACK_GLOB`), logged as `warnings.append("serena_fallback_rg_used")` — **never** silent `*.py`-only default.

### Score field ambiguity

**Fix:** Split fields on `Candidate`:

- `retrieval_score: float | None` — source-native (e.g. BM25 raw, vector distance mapped once at adapter).
- `rerank_score: float | None` — set **only** if Option B ran; otherwise omit/null.

Do **not** overload a single `score` for multiple meanings.

### Adapter normalization / signal loss

**Fix:** Each `Candidate` includes:

- `metadata: dict` — e.g. `{ "raw_score", "rank_in_source", "source_specific": { ... } }` so vector distances, BM25 internals, or graph node ids are not dropped.

---

## 1. Target retrieval architecture (after fixes)

```text
query
  → [parallel]
        graph_lookup      (symbols / nodes only — no NL planner inside)
        bm25_retrieval
        vector_retrieval
        serena_search     (MCP primary; optional explicit fallback layer)

  → RRF                 (single merge — final ranking if Option A)

  → (optional) reranker  (Option B only — single authority; NO fusion with RRF scores)

  → prune               (deterministic: dedup + preserve order + budgets)

  → output
```

**Not in default path:** `graph_expand(...)` — optional **separate** API or planner-triggered step only.

| Stage | Role |
|-------|------|
| **graph_lookup** | Deterministic graph **lookup** → list of candidates (nodes/symbols + path + snippet if available). **No** NL symbol extraction inside this function. |
| **BM25 / vector / serena** | Unchanged role: ordered lists → RRF. |
| **RRF** | **Only** merge of ranks. Under Option A, **final** ranking authority. |
| **Reranker (Option B)** | **Only** when enabled: reorders merged list; **no** weighted fusion with RRF. |
| **Prune** | Dedup by key above; preserve order; char/snippet caps; lexical path tie-break only where specified. |

---

## 2. Retrieval contract (critical)

### `RetrievalInput`

| Field | Type | Notes |
|-------|------|--------|
| `query` | `string` | Required. |

Optional: `project_root`, `top_k_per_source`, `rrf_top_n`, `enable_rerank` (Option B only).

### `Candidate`

| Field | Type | Notes |
|-------|------|--------|
| `path` | `string` | After validation/normalization. |
| `snippet` | `string` | Prefer non-empty for downstream. |
| `symbol` | `string \| None` | Optional. |
| `source` | enum | `graph` \| `bm25` \| `vector` \| `serena` |
| `retrieval_score` | `float \| None` | Source-native only. |
| `rerank_score` | `float \| None` | **Only** if Option B applied. |
| `metadata` | `dict` | `raw_score`, `rank_in_source`, `source_specific` (preserve lossless signals). |

**Rules**

- **No implicit ranking** before RRF except each source’s **list order** (rank = index).
- **RRF** is the **only** combiner under Option A.
- Under Option B, **final** order comes from **reranker only** (not RRF+ranks blended).

### `RetrievalOutput`

| Field | Type |
|-------|------|
| `candidates` | `List[Candidate]` |

Optional: `query`, `warnings[]`, `stages` telemetry.

---

## 3. Component mapping

| Old component | New role | Action |
|---------------|----------|--------|
| `graph_retriever.retrieve_symbol_context` (monolithic) | — | **Split:** replace with `graph_lookup` + optional `graph_expand`; **do not** call NL extraction in lookup. |
| `repo_graph.graph_query` / `GraphStorage` | `graph_lookup` | **Keep** as engine; thin wrapper only. |
| `bm25_retriever.search_bm25` | `bm25` | **Keep**; adapter → `Candidate` + metadata. |
| `vector_retriever.search_by_embedding` | `vector` | **Keep**; adapter → `Candidate` + metadata. |
| `serena_adapter.search_code` | `serena` | **Keep MCP**; add **explicit** optional fallback module (not implicit `*.py` grep). |
| `rank_fusion.reciprocal_rank_fusion` | RRF | **Keep**. |
| `reranker_factory` + `BaseReranker` | Option B only | **Keep** behind flag; **no** fusion weights with RRF. |
| `context_pruner` | prune | **Replace** behavior: deterministic dedup + order preservation (see §Issue 3); remove kind-based sort. |
| `retrieval_pipeline.run_retrieval_pipeline` | orchestration | **Replace** with `retrieval_pipeline_v2`. |
| `filter_and_rank_search_results` | — | **Remove** from hot path. |

---

## 4. Extraction plan (step-by-step)

### Phase A — Isolation

1. Entrypoints: `step_dispatcher._search_fn`, `run_retrieval_pipeline`, `tools/search_candidates.py`.
2. Flag: `RETRIEVAL_PIPELINE_V2` (default `0`).
3. When on: skip heuristics per prior plan; **add** graph split and rerank policy per §Critical fixes.

### Phase B — Primitive wrapping

1. `candidate_schema.py`: types including `retrieval_score`, `rerank_score`, `metadata`.
2. Adapters for bm25, vector, serena, **graph_lookup** (not full old graph_retriever).
3. Optional: `graph_expand` module **not** wired into default v2.

### Phase C — New pipeline

1. `retrieval_pipeline_v2.py`: parallel **graph_lookup**, bm25, vector, serena → RRF → (optional rerank if Option B) → **prune_deterministic**.
2. Default: **Option A** (no reranker).

### Phase D — Reranker (Option B only, later)

1. If `enable_rerank`: RRF merged list → `reranker.rerank` → **sort by `rerank_score` only**.
2. **Do not** import `RERANK_FUSION_WEIGHT` / `RETRIEVER_FUSION_WEIGHT` in v2.

### Phase E — Cleanup

1. `validate_search_paths` without scoring.
2. Remove dead fusion code paths from v2.

---

## 5. Dependency graph

```text
OLD (entangled)
  graph_retriever → NL extraction + expand + context
  RRF + rerank + weighted fusion

NEW (layered)
  graph_lookup (no query interpretation)
  bm25 | vector | serena
  RRF → [optional rerank, single authority] → prune (deterministic)
```

---

## 6. Heuristic removal plan

(Unchanged intent from prior revision; ensure **graph NL extraction** and **rerank fusion** are listed.)

| Location | What to do |
|----------|------------|
| NL symbol extraction in graph | **Remove** from `graph_lookup`; only optional `graph_expand` / external policy. |
| `RERANK_FUSION_WEIGHT` / `RETRIEVER_FUSION_WEIGHT` in v2 | **Do not use**. |
| `context_pruner` `_KIND_RANK` | **Remove** for v2 prune. |

---

## 7. Risk analysis

(Add to prior table:)

| Risk | Mitigation |
|------|------------|
| **Weaker results without graph_expand** | Enable expansion only as explicit follow-up step or flag. |
| **Option B latency** | Keep default Option A. |

**Rollback:** `RETRIEVAL_PIPELINE_V2=0`.

---

## 8. Minimal implementation plan

### New files

- `agent/retrieval/graph_lookup.py` — deterministic lookup only.
- `agent/retrieval/retrieval_pipeline_v2.py`
- `agent/retrieval/candidate_schema.py`
- `agent/retrieval/prune_deterministic.py` (or name aligned with §Issue 3)
- `agent/retrieval/serena_fallback.py` (optional explicit fallback)

### Existing files to modify

- `graph_retriever.py` — deprecate monolithic path or refactor internals into lookup vs expand.
- `step_dispatcher.py`, `retrieval_config.py`, `search_candidates.py` — as before.

---

## 9. Validation plan

### Test cases

1. **Symbol lookup:** same `query` string hits consistent nodes in `graph_lookup` vs BM25 (no hidden NL rewrite inside graph).
2. **Determinism:** same inputs → same RRF output → same prune output (byte-stable order).
3. **Option A vs B:** Option A ignores reranker; Option B order matches rerank scores only.
4. **Serena:** MCP failure → `warnings` + optional explicit fallback path logged.

### Expected improvements

- **Single ranking authority** under A (RRF); under B (rerank only after merge).
- **No graph-as-planner** in default retrieval.
- **Deterministic** prune.

### Metrics

- Hit@5 / MRR; latency without rerank (Option A) vs with (Option B).

---

This plan is a **controlled extraction**: **split graph**, **single rerank policy (no fusion)**, **deterministic prune**, **explicit Serena fallback**, and **rich `metadata`** on candidates—then **re-wire** behind `RETRIEVAL_PIPELINE_V2`.
