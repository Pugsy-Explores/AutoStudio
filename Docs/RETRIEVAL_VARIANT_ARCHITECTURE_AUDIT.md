# Retrieval Variant Architecture Audit — Code-First Design

**Context:** Post–Stage 41 plan-step query contract. Considering: multi-query variants, improved entity lookup, misspelling tolerance. Must avoid search explosion, retry explosion, fuzzy-everything chaos.

---

## 1. CURRENT QUERY-VARIANT LANDSCAPE

| Location | Symbol | Behavior | Production? |
|----------|--------|----------|-------------|
| `agent/execution/mutation_strategies.py` | `generate_query_variants(query)` | Identifier variants: underscorify, strip digits, shorten. "router eval2" → ["router_eval_v2", "router_eval2", "router_eval", "router"]. Returns list, deduped. | **No.** Imported only by `tests/test_policy_engine.py`. Policy engine imports `symbol_retry`, `retry_same` — not `generate_query_variants`. |
| `agent/retrieval/query_expansion.py` | `generate_query_expansions(query)` | Token-based expansion: original, tokens, bigrams, CamelCase, Class.method. Capped at 8. Deterministic. | **Yes.** Called from `agent/retrieval/retrieval_pipeline.py` line 145 in `search_candidates()`. |
| `agent/retrieval/query_rewriter.py` | `rewrite_query_with_context(...)` | LLM-based rewrite. Returns `str` or `list[str]` from JSON `queries` field. No cap on list size. | **Yes.** Wired as `_rewrite_for_search` in step_dispatcher, passed to policy engine as `rewrite_query_fn`. |
| `agent/retrieval/query_rewriter.py` | `heuristic_condense_for_retrieval(text)` | Strip filler words. Deterministic. | **Yes.** Used by plan_resolver, planner; exported from agent.retrieval. |
| `agent/retrieval/query_rewriter.py` | `_heuristic_rewrite_no_llm(text)` | Same as above. | **Yes.** Internal. |

**Query rewrite lists:** Policy engine `_execute_search` (lines 330–331) accepts `queries_to_try` from rewriter. Tries each sequentially until success. No cap on `queries` from LLM.

**Deterministic query expansion:** `generate_query_expansions` exists but **only `expansions[0]` is used** for BM25 and vector in `search_candidates` (lines 184, 202). Grep and repo_map use raw `query`. Expansions are computed then discarded for multi-query; effectively single-query.

**Parallel execution of multiple retrieval backends:**  
- `agent/retrieval/retrieval_pipeline.py` lines 250–258: `ThreadPoolExecutor(max_workers=4)` runs BM25, vector, grep, repo_map **on same single query** (or `expansions[0]`).  
- `agent/retrieval/search_pipeline.py`: `hybrid_retrieve` runs BM25, graph, vector, grep in parallel via `concurrent.futures` — again on **one query**.

**Fuzzy / typo / symbol / path normalization:** See §4.

---

## 2. COST MULTIPLIER AUDIT

| Location | Multiplier | Description |
|----------|------------|-------------|
| `agent/execution/policy_engine.py` `_execute_search` | **Retry loop** | `max_attempts=5` (POLICIES["SEARCH"]). Each attempt: rewriter call (LLM), then `for query in queries_to_try: _search_fn(query, state)`. |
| `_execute_search` inner loop | **Query list loop** | `queries_to_try` from rewriter; no cap. LLM can return N queries; each triggers `_search_fn`. |
| `step_dispatcher._search_fn` | **Hybrid backend fanout** | When `ENABLE_HYBRID_RETRIEVAL`: `hybrid_retrieve(query)` runs 4 backends in parallel. Else: sequential `retrieval_order` (graph → vector → grep → search_code). |
| `retrieval_pipeline.search_candidates` | **Candidate expansion** | 4 backends in parallel; then RRF merge. Single query. |
| `run_retrieval_pipeline` | **Downstream expansion** | Anchor detection, expand, read, find_references, rerank, prune. Per call, not per query. |
| Agent loop / replan | **Replan loops** | Empty SEARCH → replan. Not retrieval-internal. |

**Combinatorial blowup from naive parallel multi-query:**

```
retries (5) × variants_per_attempt (N, unbounded) × backends_per_search (4) = 20N searches per SEARCH step
```

If N=5 (LLM returns 5 queries): 100 backend invocations per SEARCH step. If we add deterministic variants (e.g. 3) before retrieval and run them in parallel: 5 × 3 × 4 = 60 per step. If variants are sequential within attempt: 5 × (1 + 2 fallbacks) × 4 = 60 worst case, but typically 1×4=4 on first success.

**Conclusion:** Any layer that multiplies queries **inside** the retry loop or **in parallel** with existing fanout causes search explosion. Safe placement: bounded deterministic variants, **sequential** try, **early exit** on first success, **no** retry-count increase.

---

## 3. ENTITY-LOOKUP GAP

| Entity | Current support | Gap |
|--------|-----------------|-----|
| **Functions** | `repo_map` exact + substring; graph retriever; symbol_query_detector for reranker bypass | Substring is case-insensitive only. No typo tolerance. |
| **Classes** | Same | Same. |
| **Variables** | Weaker — no dedicated variable index; grep/vector may find | Bad query extraction (natural language → identifier). |
| **Files** | `instruction_path_hints`, `_FILE_EXT_RE` in symbol_query_detector, path in query | Path fragment normalization is minimal. |
| **Paths** | `normalize_file_path` strips JSON artifacts only | No path-fragment normalization (e.g. `src/foo` vs `foo`). |

**Main cause from code:** **Bad query extraction** and **missing typo tolerance** in repo_map.  
- `agent/retrieval/repo_map_lookup.py`: exact match then substring (`t_lower in sym_lower or sym_lower in t_lower`). No edit distance.  
- `agent/retrieval/anchor_detector.py`: same.  
- Query reaching repo_map is `step.get("query") or step.get("description")` — user text often has typos ("StepExectuor") or phrasing ("the step executor class").  
- `generate_query_variants` (unused) would help: "step executor" → "step_executor", "step".  

**Secondary:** Repo-map lookup uses `_query_terms` (alphanumeric + underscore); no snake/camel normalization for matching.

---

## 4. MISSPELL / NORMALIZATION AUDIT

| Capability | Exists? | Location |
|------------|---------|----------|
| Typo tolerance | **No** | No edit-distance, Levenshtein, or fuzzy match. |
| Edit-distance matching | **No** | — |
| Case normalization | **Partial** | Repo_map substring match: `term_lower`, `sym_lower`. Case-insensitive substring only. |
| snake_case / camelCase / kebab-case normalization | **No** | `symbol_query_detector` detects patterns; no normalization for lookup. |
| Singular/plural normalization | **No** | — |
| Path fragment normalization | **Minimal** | `normalize_file_path` strips JSON/quote artifacts. No `path/to/file` ↔ `file` resolution. |

---

## 5. CHOSEN NEXT STAGE

**Stage 42: Bounded deterministic query-variant generation before first retrieval**

- **Scope:** One insertion point only. No LLM changes, no retrieval redesign.
- **Behavior:** From `retrieval_input` (query or description), produce a list of up to 3 queries: `[base, v1, v2]` where v1, v2 come from deterministic identifier variants (e.g. underscorify, token permutations). Reuse or adapt `generate_query_variants` semantics; hard-cap at 3 total; dedupe.
- **Execution:** Sequential try within **first** SEARCH attempt only. First query that returns non-empty results wins; no extra attempts.
- **No** parallel multi-query. No retry-count increase. No misspelling/typo handling in this stage.

---

## 6. PRODUCTION-HONEST CONTRACT AFTER THAT CUT

| Contract item | Value |
|---------------|-------|
| **Base query** | `retrieval_input = (step.get("query") or step.get("description") or "").strip()` |
| **Generated variants** | Deterministic from base: identifier-style (underscore join, digit stripping, shortened). No LLM. |
| **Max variants** | 3 total (base + up to 2). Hard constant. |
| **Dedupe** | By string equality; base first. |
| **Parallel vs sequential** | Sequential. Try base, then v1, then v2; stop on first success. |
| **Retry interaction** | Variants apply to **attempt 1 only**. Attempts 2–5 unchanged (rewriter + queries_to_try). |
| **Misspelling handling** | None in this stage. |
| **Observability** | Log which query succeeded; optional `attempt_history` entry for tries. |

---

## 7. EXACT FILES LIKELY TO CHANGE

| File | Change |
|------|--------|
| `agent/execution/policy_engine.py` | In `_execute_search`, before rewriter: compute `initial_queries = deterministic_variants(retrieval_input, max_total=3)`. On attempt 1, use `initial_queries` as `queries_to_try` if non-empty; else keep current rewriter path. |
| `agent/execution/mutation_strategies.py` | Either wire `generate_query_variants` into policy engine, or add `get_initial_search_variants(text: str, max_total: int = 3) -> list[str]` that returns `[base] + generate_query_variants(base)[:max_total-1]` capped and deduped. |
| `agent/retrieval/__init__.py` | Export new helper if added in retrieval package. |

**No changes:** `step_dispatcher`, `query_rewriter`, `retrieval_pipeline`, `search_pipeline`, `repo_map_lookup`, planner, routing.

---

## 8. RISKS / ANTI-GOALS

| Risk | Mitigation |
|------|------------|
| **Retries × variants × hybrid fanout** | Variants only in attempt 1; sequential; early exit. No new retries. |
| **Duplicate low-signal searches** | Cap 3; dedupe. Base first (often sufficient). |
| **Fuzzy matching hurting precision** | No fuzzy in this stage. |
| **Typo handling producing wrong symbol candidates** | No typo handling in this stage. |
| **Masking upstream query quality problems** | Variants are deterministic and focused; they improve extraction, not hide it. |
| **Worsening live eval runtime** | Worst case: 3 searches on attempt 1 instead of 1. Bounded. |

---

## 9. FINAL RECOMMENDATION

**Is this the right next step now?** Yes.

**What ONE exact bounded stage should be implemented next?**  
Stage 42: Bounded deterministic query-variant generation before first retrieval. Sequential try of up to 3 queries on attempt 1 only; early exit on first success. Reuse `generate_query_variants` logic; wire into policy engine.

**If no, what must be fixed first?** N/A.
