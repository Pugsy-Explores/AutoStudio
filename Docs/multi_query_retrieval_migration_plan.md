# Multi-query retrieval migration — design & plan

Staff Engineer design for a safe migration from single-query retrieval to multi-query retrieval (`retrieve_v2_multi`). This document is planning-only; implementation is tracked separately.

---

## 1. Design Summary

**Intent:** Add `retrieve_v2_multi(queries: list[str], ...)` that performs **exactly one** batched vector leg (`vector_retriever.search_batch` → `/retrieve/vector/batch` when remote-first), while **BM25, graph, repo_map/Serena, RRF, validation, and reranking stay per-query** with the same code paths and ordering semantics as today.

**Mechanism:** Refactor `retrieve_v2` so the “post-vector” pipeline (RRF → symbol boost → validate → rerank → prune) can run on **either**:

- vector rows from `fetch_vector(query, …)` (current behavior), or
- vector rows from the **i-th** slot of a precomputed batch (new behavior).

Single-query API stays a thin wrapper: `retrieve_v2(q)` = batch of one internally, or unchanged call graph with zero behavior change (implementation choice: keep literal `retrieve_v2` body and call shared `_retrieve_v2_for_query` from both).

**Dispatcher contract (must be explicit — see §1a):** Use **Option A** — extend `execute()` so it may return **`ExecutionResult | list[ExecutionResult]`**. For `SEARCH_MULTI`, return **`[ExecutionResult, …]`** in query order. **No** post-hoc “split one `ToolResult` into N results” in `Dispatcher.search_batch` (that pattern duplicates mapping logic and couples fragile layers).

**Exploration:** Stop doing **N × `execute(SEARCH)`** for the same discovery batch; instead issue **one** `SEARCH_MULTI` so vector batching actually happens. Ordering is preserved by **index alignment** end-to-end (`i → queries[i] → results[i]`).

**Vector API:** Use **`search_batch()`** from `vector_retriever` (not ad-hoc daemon HTTP). Single codepath, preserves fallback behavior inside `search_batch`.

**Fallback (must be all-or-nothing — see §1b):** If the batched vector leg fails or has invalid shape, **do not** partially proceed. Fall back to the known-good path: **`[retrieve_v2(q) for q in queries]`** (same order), with `[VECTOR_BATCH_FALLBACK]`.

---

## 1a. Dispatcher return strategy (required decision)

**Problem:** Today **1 SEARCH → 1 `ExecutionResult`**. Multi-search needs **N `ExecutionResult`s** without breaking the implicit contract.

| Approach | Behavior | Verdict |
|----------|------------|--------|
| **Option A (recommended)** | `execute()` returns `ExecutionResult \| list[ExecutionResult]`. For `SEARCH_MULTI`, return `list[ExecutionResult]` directly from the tool path after `map_tool_result_to_execution_result` (or N parallel maps). `Dispatcher.search_batch` assigns the list to the caller — **no splitting logic**. | **Choose this.** Clear mental model; avoids duplicated mapping and fragile coupling. |
| **Option B** | `execute()` always returns one `ExecutionResult`; dispatcher manually unpacks/splits. | Riskier: duplicated mapping, easy to get ordering wrong. **Do not use** unless Option A is truly blocked by an external API. |

**Implementation notes for Option A:**

- Callers of `execute()` (including tests) must handle **either** a single result or a list (or provide a small helper `normalize_to_list(result)`).
- `map_tool_result_to_execution_result` may be invoked **once per query** inside the `SEARCH_MULTI` branch, producing `list[ExecutionResult]` with **strict index alignment** to `queries`.

---

## 1b. Failure boundary inside `retrieve_v2_multi` (all-or-nothing)

**Rule:** If the batched vector step does not yield a valid, complete result set for **all** queries, **abort** the multi path and fall back entirely.

**Required structure (conceptual):**

```text
try:
    vector_results = search_batch(queries, ...)
    if invalid shape (e.g. len != len(queries), wrong types):
        raise ValueError("vector batch shape mismatch")
    # only after validation: per-query BM25/graph/Serena + RRF + rerank per query
except Exception as e:
    log [VECTOR_BATCH_FALLBACK] reason=...
    return [retrieve_v2(q) for q in queries]   # same order, known-good path
```

**Do not:** use batch vector for queries `0..k-1` and then call `retrieve_v2` only for the remainder — that mixes semantics and hides bugs.

---

## 2. Files to Modify

Per minimal surface-area constraint, **primary**:

| File | Role |
|------|------|
| `agent/retrieval/retrieval_pipeline_v2.py` | Add `retrieve_v2_multi` with §1b boundary; shared core with optional pre-fetched vector rows |
| `agent/retrieval/adapters/vector.py` | Only if a thin helper is needed — keep minimal |
| `agent_v2/runtime/dispatcher.py` | `execute` typing/behavior: `ExecutionResult \| list[ExecutionResult]`; `search_batch` consumes one `execute` that returns a **list** for `SEARCH_MULTI` (no manual split) |

**Required wiring:**

| File | Role |
|------|------|
| `execute_fn` provider (bootstrap / `step_dispatcher`) | `SEARCH_MULTI` → `retrieve_v2_multi`; produce N `ToolResult` segments or one structured payload that maps to **N** `ExecutionResult`s inside `execute` |

**Exploration:**

| File | Role |
|------|------|
| `agent_v2/exploration/exploration_engine_v2.py` | One `SEARCH_MULTI` per **group** (symbol / regex / text) — **do not** merge groups (see §8) |

**Tests:** `tests/` — functional, ordering, batch log, regression, rerank strategy (§5).

**Out of scope:** `vector_retriever.py` (already has `search_batch`); daemon script unless response shape changes.

---

## 3. Step-by-Step Plan (phased, low risk)

### Phase 0 — Contracts & invariants

- Ordering: index `i` ↔ `queries[i]` ↔ `ExecutionResult[i]`.
- Fallback: §1b all-or-nothing → full `retrieve_v2` per query.
- Legacy: `retrieve_v2(query)` unchanged.

### Phase 1 — Core pipeline (`retrieval_pipeline_v2`)

1. Extract shared post-vector path (RRF → … → prune) for **one** query given **either** `fetch_vector` output or injected batch rows.
2. Implement `retrieve_v2_multi` with **§1b** try/validate/fallback first; only then per-query work.
3. **Per-query parallelism:** allowed, but see **§7 (nested parallelism)** — cap global concurrency.
4. Keep `retrieve_v2` behavior identical; test single-query parity.

### Phase 2 — Tool / execution layer

1. Define `SEARCH_MULTI` step shape (`queries`, tuning aligned with `RetrievalInput`).
2. Branch in `execute_fn` / `execute`: call `retrieve_v2_multi`, build tool output, map to **`list[ExecutionResult]`** (Option A).

### Phase 3 — Dispatcher

1. `execute()` return type: **`ExecutionResult | list[ExecutionResult]`**.
2. `search_batch`: build one `SEARCH_MULTI` step, **one** `execute()` call, expect **`list[ExecutionResult]`** of length `len(queries)`; pass through to exploration **in order**. **No** separate split step.

### Phase 4 — Exploration (`exploration_engine_v2`)

1. Replace N× `execute(SEARCH)` with one path that yields **one** multi-execute per batch.
2. Keep **three groups** (symbol / regex / text) as **separate** multi-searches — do **not** merge into one mega-list (preserves existing semantics and caps).

### Phase 5 — Observability

- `[VECTOR_BATCH] queries=N` immediately before the single `search_batch` call **inside** the successful multi path.
- `[VECTOR_BATCH_FALLBACK] reason=...` on §1b fallback.

### Phase 6 — Validation & rollout

- Run §5; optional feature flag (`RETRIEVAL_V2_MULTI_SEARCH=1`).

---

## 4. Result mapping (explicit)

- **Primary key:** integer index `i` in `[0, len(queries))`.
- **Vector batch:** `search_batch` output aligned with `queries`; slot `i` only feeds pipeline `i`.
- **Dispatcher:** `execute` returns `ExecutionResult[i]` for `queries[i]` via **Option A** (list return), not post-split.
- **No mixing:** RRF/rerank inputs for query `i` use only vector slot `i` and BM25/graph/Serena for `queries[i]`.

---

## 5. Testing Plan (concrete)

### Reranker / ordering drift (mandatory)

Batching changes **timing** and **thread scheduling**; even with identical vector payloads, **reranker input order or ONNX/thread behavior** can shift top-K. **Do not** rely on naive exact equality multi vs single-query without controlling the reranker.

**Use at least one of:**

1. **Mock reranker** (or stub `_apply_reranker`) in CI for **deterministic** ordering tests; or  
2. **Set overlap**, not byte-identical lists: e.g. for top-K files, **Jaccard or overlap ≥ 70%** between `retrieve_v2_multi` slice and per-query `retrieve_v2` baseline, **or** same set with rank-insensitive comparison where appropriate.

**Avoid:** vague “relaxed assertions” only — that produces **false regression hunts**.

### Other tests

1. **`retrieve_v2` regression** — unchanged behavior for single query.
2. **Parity / structure** — With reranker mocked, `retrieve_v2_multi(["a","b"])` vs two `retrieve_v2` calls can match **exactly**; with real reranker, use overlap metric above.
3. **Ordering** — `len(results) == len(queries)` and `result[i]` corresponds to `queries[i]`.
4. **Batch call count** — Exactly **one** `search_batch` per successful `retrieve_v2_multi`; log `[VECTOR_BATCH] queries=N`.
5. **Dispatcher** — One `execute` for `SEARCH_MULTI`; return type is **list** of length N (Option A).
6. **Exploration smoke** — One vector batch log per symbol/text **group**, not per query.
7. **Fallback** — Force `search_batch` failure; assert **only** `[VECTOR_BATCH_FALLBACK]` path and **full** `N × retrieve_v2` behavior (no partial batch).

### Commands

- `pytest tests/retrieval/ ...` and `agent_v2` exploration tests; `LOGLEVEL=DEBUG` + grep `[VECTOR_BATCH]`.

### Performance (manual)

- Before: N vector calls; after: 1 batch per multi call (daemon logs).

---

## 6. Risks & Mitigations

| Risk | Mitigation |
|------|------------|
| **Implicit 1:1 execute contract** | **Option A:** `list[ExecutionResult]` from `execute`; document all callers |
| **Reranker “regressions”** | Mock reranker in CI and/or **top-K overlap ≥ 70%** — §5 |
| **Partial failure after batch** | **§1b** — all-or-nothing; never hybrid per-query mix |
| **Result cross-talk** | Index mapping; `len(batch) == len(queries)` assert before per-query merge |
| **Nested ThreadPool explosion** | §7 — cap total concurrent work |
| **Hidden N+1** | Code review; only dispatcher/exploration call multi entrypoint |
| **Scope creep** | BM25/graph per-query; no merged blob |

---

## 7. Nested parallelism (ThreadPool)

Multi-query may use a **per-query** thread pool; each `retrieve_v2` already uses parallelism across sources. That yields **nested** pools (multi → per-query → per-source).

**Recommendation:** Keep per-query parallelism **but cap** total worker threads (e.g. **4–6 max** across the multi-query path) so OS/thread overhead does not explode. Document the cap in code comments.

---

## 8. Exploration grouping (confirmed)

Keep **separate** multi-searches for:

- symbol batch  
- regex batch  
- text batch  

**Do not** merge into a single `SEARCH_MULTI` across categories — preserves caps, semantics, and existing discovery behavior.

---

## 9. Option B note (dispatcher-only shortcut)

If full `SEARCH_MULTI` + Option A is delayed, a **temporary** internal path may call `retrieve_v2_multi` directly from `Dispatcher.search_batch` and synthesize `list[ExecutionResult]` without going through ReAct — still prefer returning a **list** from one logical entrypoint, not string-splitting tool output. **Promote to Option A + `SEARCH_MULTI` as soon as feasible.**

---

## 10. Design strengths (keep)

- **Minimal surface area** — no full pipeline rewrite; no touching every retriever.
- **`search_batch()`** — single vector codepath; preserves daemon + local fallback behavior.
- **Index-based mapping** — `i → queries[i] → results[i]` prevents most cross-talk bugs.
- **All-or-nothing fallback** — avoids clever partial batching.
- **Exploration** — clean switch to one multi step per **group** (symbol / regex / text), not hybrid execution.

---

## 11. Readiness

**Required before coding (plan items):**

- [x] Dispatcher: **Option A** — `ExecutionResult | list[ExecutionResult]` documented  
- [x] Reranker tests: **mock and/or ≥70% top-K overlap** — documented  
- [x] `retrieve_v2_multi`: **§1b** all-or-nothing fallback — documented  

---

## Constraints (reference)

- Backward compatibility: `retrieve_v2(query)` unchanged.
- No full pipeline rewrite; no single merged blob for all queries.
- Minimal surface area: dispatcher, `retrieval_pipeline_v2`, vector adapter if needed, execute_fn wiring, exploration.
- No async queues / background workers.
- Single-user; no distributed coordination.
