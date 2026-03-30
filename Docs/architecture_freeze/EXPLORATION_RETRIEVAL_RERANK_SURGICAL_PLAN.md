# Exploration Retrieval + Selection — Root Cause Analysis & Surgical Plan

**Status:** Design only — no implementation in this document.  
**Constraints honored:** No architecture redesign, no new services or layers, no test-path heuristics, no clustering; reuse existing retrievers, `search_batch`, `create_reranker()`, and `agent_v2` config patterns.

**Related audit (behavior-only):** [`EXPLORATION_RETRIEVAL_RERANK_AUDIT.md`](./EXPLORATION_RETRIEVAL_RERANK_AUDIT.md).

---

## Step 1 — RCA (Mandatory)

### 1.1 Current pipeline (verified from code)

```text
query → multi-retrieval (3× search_batch) → merge → DISCOVERY_MERGE_TOP_K → _may_enqueue
→ _enqueue_ranked: EXPLORATION_SCOPER_K cap → scoper (optional) → selector → enqueue → … → analyzer
```

The audit’s end-to-end string is accurate; below is the same pipeline with **decision inputs** called out.

### 1.2 Answers (code-mapped)

| # | Question | Answer |
|---|----------|--------|
| **1** | **How candidates are generated** | `ExplorationEngineV2._discovery` (`agent_v2/exploration/exploration_engine_v2.py`) runs three parallel `Dispatcher.search_batch` batches (symbol / regex / text queries from `QueryIntent`). Each step executes `_search_fn` (`agent/execution/step_dispatcher.py`): optional `RETRIEVAL_PIPELINE_V2`, cache, then **`hybrid_retrieve`** if `ENABLE_HYBRID_RETRIEVAL` and non-empty, else **sequential** `retrieve_graph` → `retrieve_vector` → `retrieve_grep` (first non-empty wins), else `search_code`, then **`filter_and_rank_search_results`**. Same path for all three batches; `search_batch(..., mode=...)` does **not** appear on the SEARCH step dict (`agent_v2/runtime/dispatcher.py`). |
| **2** | **How merge works** | Ingestion keys **`(canonical_path, sym_dedupe)`** with `sym_dedupe = symbol or "__file__"`. Per key: **`max_score`** = max of `_discovery_row_score(row)` (= `float(row.get("score") or 0.0)`), **`breakdown`** per channel (`symbol` / `regex` / `text`) updated with per-channel maxima. **`candidate`** replaced only when a new row beats **`max_score`** — so **one `ExplorationCandidate` per key**, carrying the **winning row’s** snippet and a **single** `source` from that batch (not a union). |
| **3** | **Where `top_k` is applied** | After merge sort: **`[:DISCOVERY_MERGE_TOP_K]`** (default 50, `AGENT_V2_DISCOVERY_MERGE_TOP_K`). Then **`_enqueue_ranked`**: **`candidates[:EXPLORATION_SCOPER_K]`** (default 20). Selector uses **`candidates[:EXPLORATION_SELECTOR_TOP_K]`** (default 10) inside `CandidateSelector.select_batch`. |
| **4** | **What scoper receives** | Up to **`EXPLORATION_SCOPER_K`** candidates in **discovery order**. `ExplorationScoper` (`agent_v2/exploration/exploration_scoper.py`) **re-deduplicates by `file_path`** for the prompt: builds payload rows with **`file_path`, `sources`, `snippets`, `symbols`** lists per file — aggregation happens **here**, not in `_discovery`. |
| **5** | **What selector receives** | **`scoped`** (or capped list if scoper skipped). Batch JSON payload is **`file_path`, `symbol`, `source` only** (`candidate_selector.py` ~145–151) — **no snippet**. |
| **6** | **Where reranker exists and why unused** | **`create_reranker()`** + **`BaseReranker.rerank(query, docs)`** run inside **`retrieve`** in **`agent/retrieval/retrieval_pipeline.py`** (~995–1016), gated by **`RERANKER_ENABLED`**, **`RERANK_MIN_CANDIDATES`**, symbol-query bypass, etc. Exploration discovery **never calls `retrieve`**; it only uses **`_search_fn`**, which **does not** invoke the cross-encoder. So the Qwen 0.6B reranker is **unused** on the exploration discovery path **by construction**. |

---

### 1.3 Root cause — why **test files** are often selected over **implementation**

Mapped to mechanisms (not generic ML hand-waving):

| Factor | Code behavior |
|--------|----------------|
| **Weak merge aggregation** | Merge is **per `(file, symbol)`**, not per file. **Channel signal** is only **`max_score` + per-channel numeric breakdown**; the stored **`source`** is **one** literal (`graph` / `grep` / `vector`) from the winning row, **not** a union of channels that hit the same file. Multiple hits for the same path from different symbols appear as **separate** ranked rows, so **file-level** importance is **not** consolidated before ordering. |
| **Loss of signal across retrieval channels** | The three batches **do not** route to different retrievers; labels are **attribution only**. Cross-channel fusion for exploration is **max of floats on the same key**, not RRF at the engine layer. **`retrieval_pipeline.search_candidates`** (RRF + optional test downweight) is **not** used by `_search_fn`. |
| **Lack of semantic ranking before pruning** | **`DISCOVERY_MERGE_TOP_K`** (50) and **`EXPLORATION_SCOPER_K`** (20) **truncate** using **retriever `score` maxima** only — **no** cross-encoder **query–document** scoring on the exploration candidate list. |
| **Poor representation at decision layers** | **Selector** sees **path + single symbol + single source** — **no snippet**, so the model cannot ground choices in **evidence text**. **Scoper** does see **snippets**, but only **after** the list has already been ordered and cut by retriever scores; if tests float to the top early, **semantic correction is absent upstream**. |

**Summary (root cause):** Implementation and tests are **ranked by the same retriever-derived `score` and merge rules**; **no instruction-conditioned semantic rerank** runs on exploration candidates; **merge does not aggregate per-file** in `_discovery`; **selector** is **under-informed** (no snippet in payload). Together, **test files with strong lexical/embedding matches** can **dominate** the pool **before** scoper/selector can compensate.

---

## Step 2 — Minimal fix strategy

**Constraint:** Improve **candidate quality before scoper**, without redesigning the exploration loop or changing **scoper/selector LLM logic** (prompt templates and control flow stay the same).

**Target ordering (must match):**

```text
multi-retrieval
→ merge + dedupe (rich aggregation, one row per file)
→ rerank (cross-encoder, existing Qwen 0.6B via create_reranker)
→ top_k (config-driven, ~15–20 — same order of magnitude as EXPLORATION_SCOPER_K)
→ scoper
→ selector
```

**Mechanism:**

1. **Rich file-level merge** inside **`_discovery`** (or a helper it calls) so **ordering and caps** apply to **files**, not scattered `(file, symbol)` rows.
2. **Call existing `create_reranker()`** when enabled, building **one document string per file** from **instruction + file_path + symbols + snippet_summary** (see §3.2). **Sort** by reranker relevance; **then** apply **`top_k`** from config.
3. **Populate `ExplorationCandidate`** so **selector** (and scoper’s per-file aggregation) receive **`symbols`**, **`snippet_summary`**, and **channel flags** — via **schema extension** + construction only (§3.4). **Do not** rewrite scoper/selector **algorithms**; they automatically pick up richer **`ExplorationCandidate`** fields if the schema and builders supply them.

---

## Step 3 — Surgical changes (critical)

### 3.1 Merge + dedupe upgrade

**Current:** One merge entry per **`(canonical_path, sym_dedupe)`**, winner-takes-all snippet/`source`.

**Target:** **One row per `canonical_path`** with:

| Field | Rule |
|-------|------|
| **`snippet_summary`** | Concatenate **unique** non-empty snippets (cap total chars via **`EXPLORATION_SNIPPET_MAX_CHARS`** or a dedicated **`EXPLORATION_DISCOVERY_SNIPPET_MERGE_MAX_CHARS`** if merge needs a tighter bound — **must be env-driven**, default aligned with existing caps). |
| **`symbols`** | **Union** of symbols from all rows for that file (dedupe, stable order). |
| **`sources` / channel flags** | **Union** of contributing channels: which of **symbol / regex / text** (or **`graph` / `grep` / `vector`**) had a hit — stored as a **list** for JSON payloads. |
| **Retrieval score for ordering pre-rerank** | Preserve a single **`discovery_max_score`** = **max** of `_discovery_row_score` across all ingested rows for that file (same numeric spirit as today). |

**Implementation locus:** **`ExplorationEngineV2._discovery`** after `_ingest_pairs` (either replace current `(path, symbol)` merge with a second pass **`_merge_candidates_by_file`**, or ingest into file-level buckets from the start — **same module**, no new package).

---

### 3.2 Reranker integration

| Item | Specification |
|------|----------------|
| **Position** | **After** file-level merge + **`_may_enqueue`**, **before** the **post-rerank `top_k`** slice. |
| **Function** | **`create_reranker()`** from **`agent/retrieval/reranker/reranker_factory.py`** (same singleton as main pipeline). Respect **`RERANKER_ENABLED`** from **`config/retrieval_config.py`**; add **`EXPLORATION_DISCOVERY_RERANK_ENABLED`** (default **1**) in **`agent_v2/config.py`** so exploration can be toggled without disabling global reranking. |
| **API** | **`BaseReranker.rerank(query: str, docs: list[str])` → `list[tuple[str, float]]`** sorted by score descending (`agent/retrieval/reranker/base_reranker.py`). |
| **Query (`rank_query`)** | **`ex_state.instruction`** or caller-provided instruction string already available to **`_enqueue_ranked`** / discovery caller — thread **`instruction`** into **`_discovery`** for rerank (signature extension). |
| **Doc string per file (input)** | Single UTF-8 string, e.g. **`f"{file_path}\nSymbols: {joined}\n{snippet_summary}"`** — **no** path-type heuristics; content is **neutral** factual aggregation. |
| **Output** | **Relevance score** per doc from reranker; **reorder** `ExplorationCandidate` list by score. Optional **fusion** with `discovery_max_score` using existing weights **`RERANK_FUSION_WEIGHT` / `RETRIEVER_FUSION_WEIGHT`** from **`config/retrieval_config.py`** **only if** we keep a **numeric** pre-score on the candidate (mirrors **`_apply_reranker_scores`** in **`retrieval_pipeline.py`**). If fusion is used, **duplicate-snippet** keying in **`_apply_reranker_scores`** must **not** be copied blindly — **index- or `file_path`-aligned** fusion is safer for exploration. **Pure rerank order** (sort by rerank score only) is acceptable if fusion adds risk; **toggle** via **`EXPLORATION_DISCOVERY_RERANK_USE_FUSION`** (0/1, default **1** to match main pipeline behavior). |
| **Failure** | On exception or `create_reranker() is None`, **fall back** to **pre-rerank order** (`discovery_max_score` descending), log once (mirror **`retrieval_pipeline`** fallback). |

**Min candidates:** Reuse **`RERANK_MIN_CANDIDATES`** or add **`EXPLORATION_DISCOVERY_RERANK_MIN_CANDIDATES`** if exploration should rerank with **fewer** files than the global gate — **must be config**, default consistent with global policy.

---

### 3.3 Top‑k adjustment

**Current:** **`DISCOVERY_MERGE_TOP_K`** cuts **after** merge **before** any semantic rerank (**`top_k` before rerank** relative to the **target** design).

**Target:**

| Stage | Config (new or reused) |
|-------|-------------------------|
| **Max pool entering rerank** | **`EXPLORATION_DISCOVERY_PRERERANK_POOL_MAX`** — upper bound on **file-level** rows passed to rerank (replaces the **semantic** role of raw **`DISCOVERY_MERGE_TOP_K`** for ordering quality; **migration:** set default equal to current **`DISCOVERY_MERGE_TOP_K`** for continuity, or keep **`DISCOVERY_MERGE_TOP_K`** as alias for one release — **document in changelog**). |
| **After rerank** | **`EXPLORATION_DISCOVERY_POST_RERANK_TOP_K`** — default **18** (within 15–20), **`AGENT_V2_`** prefix. This list flows into **`_enqueue_ranked`**, which still applies **`EXPLORATION_SCOPER_K`** if that cap should remain a **prompt budget** — **recommended:** **`EXPLORATION_DISCOVERY_POST_RERANK_TOP_K ≤ EXPLORATION_SCOPER_K`** by default so scoper does not re-truncate arbitrarily; align defaults accordingly. |

**Net effect:** **`top_k` after rerank**, not before semantic ordering.

---

### 3.4 Candidate payload upgrade

**Schema (`agent_v2/schemas/exploration.py` — `ExplorationCandidate`):**

- Keep **`file_path`** (maps to user’s **`file`** in JSON).
- Add optional **`symbols: list[str] = Field(default_factory=list)`**; retain **`symbol: Optional[str]`** for backward compatibility — e.g. **primary** = `symbols[0]` if non-empty else **`None`**, or document **`symbol`** as **deprecated alias** for first symbol (callers already use **`symbol`**).
- Add **`snippet_summary: Optional[str]`** (aggregated text for rerank + prompts).
- Add **`source_channels: list[Literal["graph","grep","vector"]]`** (or **`["symbol","regex","text"]`** mapped consistently — **one** convention only) for **`sources`** in JSON.
- Existing **`source`** field: set to **primary** channel for legacy readers, or deprecate in favor of **`source_channels`**.

**Selector (`candidate_selector.py`):** Extend **`payload`** dict to include **`symbols`, `snippet_summary`, `source_channels`** (and JSON-serialize consistently). **Minimal slice:** no change to **control flow** or **JSON schema keys the model must emit** — only **richer `candidates_json` content**. Optional later: **`exploration.selector.batch`** YAML adds placeholders for new fields — **not required** for the first implementation if the model can use longer structured paths in existing **`file_path`** / **`symbol`** fields; richer payload still improves **traceability** and **downstream** prompt edits.

**Scoper:** Already aggregates **per file**; with **one candidate per file** upstream, **`_aggregate_payload_by_file_path`** becomes **pass-through** for row count (still valid). **No structural change required** if **`ExplorationCandidate`** carries **`snippet_summary` / `symbols` / `source_channels`**.

---

## Step 4 — What NOT to change

Explicitly **out of scope** for this surgical plan:

- **Exploration loop** (step limits, stagnation, backtracking, queue semantics).
- **Analyzer** (`UnderstandingResult`, sufficiency, gaps).
- **Expand / refine** graph logic and cooldowns.
- **`query_intent_parser`** (handled elsewhere).
- **New microservices**, **new retrieval daemons**, **new vector stores**.
- **Heuristic penalties** for paths containing `test` / `spec` / etc.

---

## Step 5 — Implementation plan (JSON)

```json
{
  "files_to_modify": [
    "agent_v2/exploration/exploration_engine_v2.py",
    "agent_v2/exploration/candidate_selector.py",
    "agent_v2/schemas/exploration.py",
    "agent_v2/config.py",
    "agent/prompt_versions/exploration.selector.batch/v1.yaml (optional — only if exposing new fields in rendered prompt)"
  ],
  "functions_to_modify": [
    "ExplorationEngineV2._discovery",
    "ExplorationEngineV2._enqueue_ranked (pass-through ordering assumptions only if signature threading for instruction)",
    "CandidateSelector.select_batch (payload dict construction)",
    "Optional: small helper module function in agent_v2/exploration/ e.g. discovery_rerank.py — only if _discovery becomes too long; prefer private methods on the engine class to avoid new public abstractions"
  ],
  "new_fields_added": [
    "ExplorationCandidate.symbols",
    "ExplorationCandidate.snippet_summary",
    "ExplorationCandidate.source_channels",
    "ExplorationCandidate.discovery_rerank_score (optional float for telemetry / fusion)",
    "ExplorationCandidate.discovery_max_score (optional; may replace setattr on private attrs)"
  ],
  "reranker_integration_point": "End of ExplorationEngineV2._discovery: after file-level merge and _may_enqueue filter; before EXPLORATION_DISCOVERY_POST_RERANK_TOP_K slice; uses create_reranker() from agent/retrieval/reranker/reranker_factory.py and BaseReranker.rerank(instruction, doc_strings)",
  "merge_logic_changes": "Replace (path,symbol) keyed merge with file-keyed aggregation: union symbols, merged snippet_summary, union source channels, max retriever score; sort pool by max score then rerank",
  "top_k_changes": "DISCOVERY_MERGE_TOP_K superseded or complemented by EXPLORATION_DISCOVERY_PRERERANK_POOL_MAX (pre-rerank) and EXPLORATION_DISCOVERY_POST_RERANK_TOP_K (post-rerank, 15–20 band)",
  "estimated_lines_changed": "medium",
  "risk_level": "medium"
}
```

**Risk notes:** Schema extension must remain **backward compatible** (defaults for new fields). Rerank adds **latency** per discovery call — mitigate with existing **daemon** path (`RERANKER_USE_DAEMON`) and **caching** inside **`BaseReranker`**.

---

## Step 6 — Validation plan

| Metric | Before (baseline) | After (expected direction) |
|--------|-------------------|----------------------------|
| **% test files in selector/scoper input** | Measure from logged candidate **`file_path`** (substring / project’s test-dir convention for **labeling only** in eval harness, not in production code). | **Lower** share of test paths in **top `EXPLORATION_DISCOVERY_POST_RERANK_TOP_K`**. |
| **Implementation files in top pool** | Count **`src/` / `lib/`** (project-specific eval labels) appearing in **`DISCOVERY_MERGE_TOP_K`** list. | **Higher** recall of implementation paths **after rerank** vs **before** on fixed benchmark tasks. |
| **“Insufficient” / low-evidence loops** | **`UnderstandingResult.evidence_sufficiency == "insufficient"`** rate or analyzer-driven **refine** count per task (from existing exploration eval logs). | **Reduction** in consecutive insufficient steps where root cause was **wrong file choice**. |

**Harness:** Reuse **`exploration_behavior_eval_harness`** / saved JSON artifacts under **`artifacts/exploration_eval_logs/`**; add **before/after** comparison on the same **frozen** case set.

**A/B toggles:** **`EXPLORATION_DISCOVERY_RERANK_ENABLED=0`** restores **merge + top_k** without rerank for regression comparison.

---

## Config inventory (all numeric — env-driven)

| Constant | Purpose |
|----------|---------|
| **`EXPLORATION_DISCOVERY_RERANK_ENABLED`** | Master toggle for cross-encoder in `_discovery`. |
| **`EXPLORATION_DISCOVERY_PRERERANK_POOL_MAX`** | Max file-level candidates **into** rerank. |
| **`EXPLORATION_DISCOVERY_POST_RERANK_TOP_K`** | Pool size **after** rerank (15–20 band). |
| **`EXPLORATION_DISCOVERY_RERANK_MIN_CANDIDATES`** | Optional; else **`RERANK_MIN_CANDIDATES`**. |
| **`EXPLORATION_DISCOVERY_RERANK_USE_FUSION`** | Fuse rerank + retriever vs pure rerank order. |
| **`EXPLORATION_DISCOVERY_SNIPPET_MERGE_MAX_CHARS`** | Cap merged **`snippet_summary`** (if distinct from **`EXPLORATION_SNIPPET_MAX_CHARS`**). |

Existing **`RERANKER_*`**, **`DISCOVERY_*`**, **`EXPLORATION_SCOPER_K`**, **`EXPLORATION_SELECTOR_TOP_K`** remain authoritative for shared behavior.

---

## Expected outcome

- **Higher-quality** candidate pool **before** scoper: **semantic** ordering aligned with **user instruction**, not only retriever **`score`** maxima on fragmented keys.
- **Reranker** used on a path where it was **previously unreachable**.
- **Scoper / selector** receive **richer, file-centric** evidence (**symbols**, **`snippet_summary`**, **channels**) without **new services** or **loop** changes.
- **Bottleneck shift:** From **merge + early truncation without semantic rank** toward **controlled pool + rerank + small top_k** — **scoper/selector** operate on **better inputs** by **data** and **order**, not by **prompt-only** course correction.
