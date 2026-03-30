# Exploration: Retrieval → Merge → Reranking → Scoper — Code Audit

**Scope:** Describe **current** behavior only (no recommendations). All claims below are tied to `agent_v2/exploration/*`, `agent/execution/step_dispatcher.py`, `agent/retrieval/*`, and `agent_v2/runtime/dispatcher.py` as read in-repo.

---

## Step 1 — Candidate Flow (`ExplorationEngineV2._discovery`)

**Entry:** `agent_v2/exploration/exploration_engine_v2.py` — `ExplorationEngineV2._discovery`.

### 1.1 Query → `search_batch` → results

1. **Query lists** are built from `QueryIntent`:
   - `symbol_queries`: up to `DISCOVERY_SYMBOL_CAP` (default **8**, `agent_v2/config.py`)
   - `regex_queries`: up to `DISCOVERY_REGEX_CAP` (default **6**)
   - `text_queries`: from `intent.keywords`, up to `DISCOVERY_TEXT_CAP` (default **6**), optionally merged with `discovery_keyword_inject` (≤2)

2. **Three parallel batches** run via `ThreadPoolExecutor(max_workers=3)`, each calling  
   `_collect_pairs(query_type, queries)` → `self._dispatcher.search_batch(queries, state, mode=query_type, ...)`  
   (`exploration_engine_v2.py` ~1178–1184, 1163–1176).

3. Each `search_batch` item invokes `Dispatcher.execute` with a SEARCH-shaped step (query only; **`mode` is not attached to the step dict** — `agent_v2/runtime/dispatcher.py` ~159–171).

4. **Per-row ingestion:** For each `ExecutionResult`, `data = res.output.data`, `raw = data.get("results") or data.get("candidates")`. Every dict row becomes a candidate **per row** (no cap inside `_ingest_pairs` beyond what the SEARCH tool returns).

### 1.2 How many candidates initially?

- **There is no fixed “N candidates per channel” in the engine.** The number of rows equals the sum of `len(results)` over **all** queries in **all** three batches, before deduplication.
- **Practical upper bound** is whatever each SEARCH returns (e.g. hybrid merge capped by `MAX_SEARCH_RESULTS` in `agent/retrieval/search_pipeline.py` for `hybrid_retrieve`).

### 1.3 Merge

- Rows are keyed by `(canonical_path, sym_dedupe)` where `sym_dedupe = symbol if symbol else "__file__"` (`exploration_engine_v2.py` ~1213–1217).
- For each key, the engine keeps **`max_score`** = max of `_discovery_row_score(row)` (i.e. `float(row.get("score") or 0.0)` with exception fallback to `0.0`) across **all** ingestions, and **per-channel** maxima in `breakdown["symbol"|"regex"|"text"]` (~1227–1243).
- **Candidate object** stored is the row that achieved the current **max_score** (when a higher score wins, `candidate` is replaced ~1241–1243).

### 1.4 Top‑K after merge

- Merged keys are sorted by **`max_score` descending**, then sliced to **`DISCOVERY_MERGE_TOP_K`** (default **50**, env `AGENT_V2_DISCOVERY_MERGE_TOP_K`) — `exploration_engine_v2.py` ~1249–1253.

### 1.5 Post–top‑K filter

- Each surviving row is appended to `deduped` only if `_may_enqueue(ex_state, file_path, symbol)` (~1265–1266). **Final list length ≤ `DISCOVERY_MERGE_TOP_K`** after filtering.

### 1.6 Next stage

- **`_enqueue_ranked`** receives this `deduped` list (see Step 5).

---

## Step 2 — Retrieval Channels (symbol / regex / text)

| Exploration label | `ExplorationCandidate.source` | What actually runs per SEARCH |
|-------------------|------------------------------|------------------------------|
| `symbol` batch | `graph` | Same **`_search_fn`** as every SEARCH (`agent/execution/step_dispatcher.py` ~158–352). **No** branch in `_search_fn` reads `mode` from the step. |
| `regex` batch | `grep` | Same as above. |
| `text` batch | `vector` | Same as above. |

**Routing inside `_search_fn` (order matters):**

1. Optional `RETRIEVAL_PIPELINE_V2` → `retrieve_v2_as_legacy` (~179–184).
2. Repo map / anchor side effects on `state.context` (~186–199).
3. Retrieval cache hit (~201–210).
4. If **`ENABLE_HYBRID_RETRIEVAL`**: `hybrid_retrieve(query, state)` from `agent/retrieval/search_pipeline.py` (~212–224). On success, **returns immediately** (no per-channel isolation).  
   - `hybrid_retrieve` runs **BM25, graph, vector, grep** in parallel and merges with **RRF** if `ENABLE_RRF_FUSION`, else concatenation merge (`search_pipeline.py` ~80–159).
5. Else: **sequential** loop over `retrieval_order = _get_retrieval_order(state.context.get("chosen_tool"))` — default `["retrieve_graph", "retrieve_vector", "retrieve_grep"]` (`step_dispatcher.py` ~145–155, 228–277). **First retriever that returns non-empty `results` wins** (`break` after each success).
6. Fallback `search_code` (~280–283).
7. **Always** (if `filter_and_rank_search_results` path): `filter_and_rank_search_results` on the result list (~315–329).

**Conclusion on “routing enforced”:** Exploration’s **`mode` argument to `search_batch` does not change `_search_fn` behavior** in the inspected code. Channel labels (`graph` / `grep` / `vector`) are **attributed in exploration merge** from the **batch** that produced the row, not from distinct retriever wiring inside a single SEARCH.

**Comparability of scores across channels:** Merge uses **`float(row.get("score") or 0.0)`** from each row. Scores come from whichever path succeeded (hybrid RRF output, or graph/vector/grep retriever, then filtering). **No RRF inside `ExplorationEngineV2`** — only **max** and **sort by max_score**.

---

## Step 3 — Merge Logic (Exploration Engine)

### 3.1 RRF in exploration merge?

**No.** `reciprocal_rank_fusion` is **not** called in `_discovery`. Exploration merge is:

- **Dedup key:** `(canonical_file_path, symbol_or___file__)`
- **Aggregate:** `max_score` across rows sharing that key; **per-channel** max in `breakdown`
- **Global ordering:** sort by **`max_score` descending**
- **Cut:** `[:DISCOVERY_MERGE_TOP_K]`

### 3.2 Score-based vs position-based?

**Score-based** for ordering (`max_score` from retrieval rows).

### 3.3 Do all channels contribute equally?

They contribute **only** through **max scores** on shared keys. A channel that only hits different keys does not “vote” for the same key. There is **no** explicit equal-weight fusion across channels.

### 3.4 Does symbol search dominate?

**Not structurally.** Dominance is **whichever retrieved rows produce the highest numeric `score`** after the underlying SEARCH path. The **`source` tag** (`graph`/`grep`/`vector`) does not add a separate weight in merge — only **scores** and **breakdown** metadata.

**Note:** A separate RRF path exists in **`retrieval_pipeline.search_candidates`** (`agent/retrieval/retrieval_pipeline.py` ~150–326) with test-path downweighting — that function is **not** invoked by `_search_fn` for exploration SEARCH (see Step 4).

---

## Step 4 — Reranker (Qwen 0.6B) — Where It Lives

### 4.1 Model identity

- Default GPU reranker model string: **`Qwen/Qwen3-Reranker-0.6B`** in `agent/models/model_config.py` (`_DEFAULT_RERANKER` / `gpu_model`).

### 4.2 Where it is invoked

- **`create_reranker()`** and **`_reranker.rerank(rank_query, snippets)`** appear in **`agent/retrieval/retrieval_pipeline.py`** inside the **full retrieval / context-build pipeline** (~995–1016), gated by `RERANKER_ENABLED`, `is_symbol_query` bypass, `RERANK_MIN_CANDIDATES`, and candidate budget.

- **`_search_fn`** (used for exploration SEARCH) **does not** call `create_reranker` or cross-encoder rerank. It ends with **`filter_and_rank_search_results`**, not reranker fusion.

### 4.3 Stage relative to exploration discovery

- The **Qwen cross-encoder reranker runs in the main `retrieve` pipeline** when building ranked context for the planner path that uses **`retrieval_pipeline.retrieve`**.  
- **Exploration `EngineV2` discovery** uses **`Dispatcher` → `_search_fn`** only. **That path does not include the Qwen reranker.**

### 4.4 If reranker ran (main pipeline only): inputs and behavior

- **Input:** `rank_query` and **snippet strings** from deduped candidates (`coerce_snippet_text`); rerank is over **snippets**, fused via `_apply_reranker_scores` with `RERANK_FUSION_WEIGHT` (`retrieval_pipeline.py` ~350+).

- **Reorders** and **slices** via `RERANKER_TOP_K` / fusion — **not** the same as exploration’s `CandidateSelector`.

---

## Step 5 — Scoper vs “Reranking” (Exploration)

**Order in `_enqueue_ranked`** (`exploration_engine_v2.py` ~1281–1374):

1. Filter candidates with `_may_enqueue`.
2. **`capped = candidates[:EXPLORATION_SCOPER_K]`** (default **20**, `EXPLORATION_SCOPER_K`).
3. **`ExplorationScoper.scope`** when `need_scope_llm = (self._scoper is not None) and len(capped) > EXPLORATION_SCOPER_SKIP_BELOW` (default skip when **≤ 5**). The scoper instance is injected by the runner (`exploration_runner.py` gates construction on `ENABLE_EXPLORATION_SCOPER`). **Scoper** returns a **subset** of candidates (LLM indices → expand to files).
4. **`CandidateSelector.select_batch`** on **`scoped`** (or `capped` if scoper skipped): LLM JSON → **`selected_indices` / `selected`** → matched to **`ExplorationCandidate`** rows. **Limit** = `min(limit, len(scoped))` where `limit` is passed from caller (e.g. **5** initial enqueue, **3** relaxed recovery).

**Answer to “A / B / C”:**

- **Scoper runs before** `CandidateSelector` (not after).
- **Scoper does not replace** `CandidateSelector`; the selector always runs after (unless scoper errors in strict mode — not analyzed here).
- The **Qwen cross-encoder reranker does not run between** discovery merge and scoper in the exploration code path audited.

**What “reranking” means in exploration:**

- **Discovery:** sort by `max_score` + `DISCOVERY_MERGE_TOP_K`.
- **Scoper:** LLM **filters** candidate **files** (subset selection).
- **CandidateSelector:** LLM **selects** up to `limit` candidates from the scoper output; **payload fields** to the LLM are **`file_path`, `symbol`, `source` only** (`agent_v2/exploration/candidate_selector.py` ~145–151). **Snippets are not in the selector JSON payload** (snippets **are** in scoper payload via `exploration_scoper.py` aggregation).

**Fallback:** If selector cannot match model output to candidates, **`select_batch` returns `top[:min(limit, len(top))]`** in **discovery/scoper order** (`candidate_selector.py` ~274–281).

---

## Step 6 — Failure Analysis: Why Tests Can Win (Code-Anchored)

Tied to **verified** mechanisms:

1. **Retrieval (`_search_fn`):** Results are whatever **hybrid** or **first-success sequential** retrieval returns, then **`filter_and_rank_search_results`**. No test downweight in this path (unlike `retrieval_pipeline.search_candidates`**’** RRF path which applies `RETRIEVAL_TEST_DOWNWEIGHT` — **not** used by `_search_fn`).

2. **Merge:** Pure **max score** by `(file, symbol)`. Test files that appear with **high `score`** from vector/grep/hybrid **rank** like any other file.

3. **Cross-encoder reranker:** **Not applied** in exploration discovery; **no** Qwen rerank correction before exploration merge.

4. **Scoper:** Prompt asks to prefer implementation over tests (`agent/prompt_versions/exploration.scoper/v1.yaml`), but **input** is **aggregated snippets + paths**; no separate “definition vs reference” metadata in schema. **If** retrieval fills the cap with test-heavy files, scoper only sees that pool.

5. **CandidateSelector:** Sees **`file_path`, `symbol`, `source` only** — **no snippet** in the batch prompt payload. Misclassification can persist if **path/symbol** look plausible.

**Bottleneck (factual):** Primary ordering pressure is **retrieval scores + merge max_score**; **LLM scoper/selector** operate on **downstream subsets** without cross-encoder rerank in this path.

---

## Step 7 — Summary JSON

```json
{
  "pipeline_order": "QueryIntent → _discovery: parallel search_batch(symbol|regex|text) → each SEARCH → _search_fn (hybrid_retrieve OR sequential retrievers + filter_and_rank) → merge by (file,symbol) max_score → sort → DISCOVERY_MERGE_TOP_K → _may_enqueue → _enqueue_ranked: take [:EXPLORATION_SCOPER_K] → ExplorationScoper.scope (optional) → CandidateSelector.select_batch → enqueue ExplorationTargets",
  "retrieval_channels": "Exploration labels three batches; Dispatcher.search_batch does not pass mode into step; _search_fn uses ENABLE_HYBRID_RETRIEVAL hybrid_retrieve (RRF inside search_pipeline) or sequential retrieve_graph/retrieve_vector/retrieve_grep by chosen_tool; ExplorationCandidate.source tags graph|grep|vector from batch only",
  "merge_strategy": "Exploration_engine merge: max per-key score, sort descending, top DISCOVERY_MERGE_TOP_K — not RRF; no reciprocal_rank_fusion in _discovery",
  "reranker_position": "Qwen cross-encoder reranker (create_reranker) in retrieval_pipeline.retrieve only; not invoked from _search_fn used by exploration discovery",
  "scoper_position": "After discovery merge cap and before CandidateSelector (answer: before selector LLM; not after cross-encoder reranker because reranker not used in this path)",
  "top_k_stage": "DISCOVERY_MERGE_TOP_K (default 50) after merge sort; EXPLORATION_SCOPER_K (default 20) pre-scoper; EXPLORATION_SELECTOR_TOP_K (default 10) input to selector; final enqueue limit from caller (e.g. 5 or 3)",
  "root_cause_of_failure": "Exploration discovery omits retrieval_pipeline.search_candidates RRF+test downweight and omits cross-encoder rerank; ranking is dominated by per-row scores in merge plus LLM scoper/selector without snippet in selector payload",
  "confidence": "high"
}
```

---

## File References (Primary)

| Topic | File |
|-------|------|
| Discovery merge & caps | `agent_v2/exploration/exploration_engine_v2.py` (`_discovery`, `_enqueue_ranked`) |
| SEARCH execution | `agent/execution/step_dispatcher.py` (`_search_fn`) |
| Hybrid RRF | `agent/retrieval/search_pipeline.py` (`hybrid_retrieve`, `_merge_results_rrf`) |
| search_candidates RRF + test downweight | `agent/retrieval/retrieval_pipeline.py` (`search_candidates` — separate from `_search_fn`) |
| Cross-encoder reranker | `agent/retrieval/retrieval_pipeline.py` (~995–1048), `agent/retrieval/reranker/reranker_factory.py` |
| Dispatcher `search_batch` | `agent_v2/runtime/dispatcher.py` |
| CandidateSelector payload | `agent_v2/exploration/candidate_selector.py` |
| Scoper | `agent_v2/exploration/exploration_scoper.py` |
| Config defaults | `agent_v2/config.py` (`DISCOVERY_*`, `EXPLORATION_SCOPER_*`, `EXPLORATION_SELECTOR_TOP_K`) |
