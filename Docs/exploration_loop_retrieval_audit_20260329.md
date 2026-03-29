# Exploration + retrieval pipeline audit (control flow, loops, candidates)

**Scope:** `ExplorationEngineV2`, `_discovery` / `_run_discovery_traced`, context feedback tracing, query intent refinement, candidate selection.  
**Date:** 2026-03-29  
**Method:** Static trace through production code paths (no speculation). **RRF:** This stack does **not** implement Reciprocal Rank Fusion; discovery uses per-file merge, score sort, optional cross-encoder rerank with optional score fusion (`RERANK_FUSION_WEIGHT` / `RETRIEVER_FUSION_WEIGHT`), then hard top‑k caps.

---

## 1. Control flow diagram (text)

```
explore()
  └─ _explore_inner()
       ├─ ExplorationState + ExplorationWorkingMemory created (ephemeral per run)
       ├─ QueryIntentParser.parse(instruction)  [LLM]
       ├─ _run_discovery_traced("initial") → span "exploration.discovery"
       │    └─ _discovery(intent, state, ex_state)
       │         ├─ Build query lists: symbols / regex / text (capped: DISCOVERY_*_CAP)
       │         ├─ Parallel: dispatcher.search_batch per channel (ThreadPoolExecutor)
       │         ├─ Ingest hits → file_merge[canonical_path] (max_score, symbols, snippets…)
       │         ├─ Build ExplorationCandidate per file → sort by discovery_max_score
       │         ├─ Cap: EXPLORATION_DISCOVERY_PRERERANK_POOL_MAX
       │         ├─ Filter: _may_enqueue_file_candidate / _may_enqueue (explored keys, excluded_paths, pending)
       │         ├─ _discovery_rerank_candidates (optional; min candidate thresholds; fusion sort)
       │         └─ Slice: EXPLORATION_DISCOVERY_POST_RERANK_TOP_K → return candidates
       ├─ memory.ingest_discovery_candidates(candidates)
       ├─ [Optional] Initial query retry (single block, not a loop — see §2)
       │    ├─ context_feedback from memory + ex_state.seen_symbols / seen_files
       │    ├─ _log_exploration_context_feedback_trace("initial_query_retry", …)
       │    ├─ QueryIntentParser.parse(…, previous_queries=prev, failure_reason=…, context_feedback=…)
       │    └─ _run_discovery_traced("retry"); maybe replace intent/candidates if _has_retry_improvement
       └─ _enqueue_ranked → ExplorationScoper (optional LLM) → CandidateSelector.select_batch (LLM)
            └─ ExplorationTarget list → ex_state.pending_targets

while ex_state.steps_taken < EXPLORATION_MAX_STEPS:
  ├─ break if termination_reason == "no_relevant_candidate"
  ├─ if not pending_targets:
  │    ├─ optional "relaxed_recovery" discovery once (if memory gaps & not yet used)
  │    └─ else termination_reason = "pending_exhausted" → break
  ├─ pop target; if (file,symbol) already in explored_location_keys → stagnation_counter++ ; continue (steps_taken NOT incremented)
  │    └─ if stagnation_counter >= EXPLORATION_STAGNATION_STEPS → "stalled" → break
  ├─ ex_state.steps_taken += 1
  ├─ InspectionReader.inspect_packet → read_snippet
  ├─ If meaningful new evidence: build context → UnderstandingAnalyzer → EngineDecisionMapper → memory updates
  │    └─ _update_utility_and_should_stop → may break "no_improvement_streak"
  ├─ _should_stop / _should_stop_pre → may break (max_steps, primary_symbol_sufficient, …)
  ├─ action = _next_action + refine cooldown + refine→expand coercion + oscillation guard
  ├─ if expand: GraphExpander.expand → _prefilter_expansion_targets → _enforce_direction_routing (may return [])
  │    └─ _enqueue_targets → continue
  └─ if refine: QueryIntentParser.parse(…, context_feedback…) → intent replaced
       → _run_discovery_traced("refine") → memory.ingest… → _enqueue_ranked
       → if still no pending → "no_relevant_candidate" → break

Finalize: completion_status, termination_reason, FinalExplorationSchema via ExplorationResultAdapter
```

**Feedback → next iteration:** Context feedback is built from `memory.get_summary()` plus `ex_state.seen_symbols` / `seen_files`, logged via `_log_exploration_context_feedback_trace` (log line `exploration.context_feedbacktrace`, Langfuse event `exploration.context_feedbacktrace`). It is passed to `QueryIntentParser.parse` on initial retry and on loop **refine** only (not on every step).

---

## 2. Loop termination analysis

| Mechanism | Location | Notes |
|-----------|----------|--------|
| **Main loop condition** | `exploration_engine_v2.py` ~581 | `while ex_state.steps_taken < EXPLORATION_MAX_STEPS` (default **5**, `agent_v2/config.py` ~29) |
| **Early exit: no candidates to inspect** | ~578–580, ~1042–1044 | `termination_reason = "no_relevant_candidate"` when `_enqueue_ranked` reports no selection and queue empty |
| **Early exit: queue drained** | ~610–611 | `"pending_exhausted"` |
| **Stagnation (duplicate targets)** | ~616–620, ~812–815 | `stagnation_counter` increments when popping already-explored `(path, symbol)` or duplicate evidence; **`steps_taken` not incremented** on duplicate pop; break at `EXPLORATION_STAGNATION_STEPS` (default **3**) → `"stalled"` |
| **Utility stop** | ~778–785, `_update_utility_and_should_stop` ~1903–1961 | If `ENABLE_UTILITY_STOP`, break after `EXPLORATION_UTILITY_NO_IMPROVEMENT_STREAK` (default **2**) steps without “improvement” per signature heuristic |
| **Explicit stop decisions** | `_should_stop` ~2250 | `max_steps` inside loop via decision path; also `primary_symbol_sufficient`, `relationships_satisfied` |
| **Pre-inspect stop** | `_should_stop_pre` ~2273 | Same success predicates before/after inspect in some paths |
| **Policy** | ~678–681 | Wrong inspection tool → `"policy_violation_full_read"` |
| **Final default** | ~1061–1062 | If `termination_reason == "unknown"`, map to `max_steps` vs `stopped` |

**Max iterations:** Bounded by `EXPLORATION_MAX_STEPS` **and** implicit work: each **meaningful** loop iteration consumes one `steps_taken`. Duplicate-queue visits do not increment `steps_taken` but consume loop turns until stagnation exit.

**Convergence:** There is **no** convergence proof or monotonic score guarantee. Termination is **operational** (caps, stagnation, utility streak, empty queue).

**Initial query retry:** Not a `while`; a **single** `if` block (~456–553). `EXPLORATION_MAX_QUERY_RETRIES` default is **1** (`agent_v2/config.py` ~75) — at most one refinement attempt in that block.

---

## 3. Progression check

| Signal | Enforced? | Where |
|--------|-----------|--------|
| `steps_taken` | Increments only when a **non-duplicate** target is processed | ~633 |
| Candidate count / score | `_has_retry_improvement` only for **initial** retry (~533–538) | Compares count and `top_score` |
| Utility “progress” | Partial — `_update_utility_and_should_stop` compares `(sufficient, relevance, gap_count)` signatures | ~1915–1926 |

**Can the loop repeat without improvement?** Yes:

- Multiple iterations can occur with analyzer producing non-improving understanding until utility streak fires (if enabled).
- Refine can re-run discovery with a **new** LLM intent; there is no strict monotonic “best score” across refinements except initial-retry improvement check.
- Duplicate target pops advance **stagnation_counter** without advancing `steps_taken`.

---

## 4. Query expansion logic

| Stage | Behavior | Caps / dedup |
|-------|----------|----------------|
| Intent parse | LLM outputs `QueryIntent` | N/A |
| Refine parse | `QueryIntentParser._remove_repeated_queries` strips symbols/keywords/regex/intents **already present** in `previous_payload` | Can **remove all** channels if the model repeats prior terms (~198–214 `query_intent_parser.py`) |
| Discovery | `dict.fromkeys` + `[:DISCOVERY_*_CAP]` per channel | Hard caps on symbol/regex/text query count |
| Gap-driven inject | `ex_state.discovery_keyword_inject` merged into text queries, max **2** | Cleared after use in `_discovery` (~1266–1271) |
| Post-merge | Sort + prererank pool + post-rerank top‑k | `EXPLORATION_DISCOVERY_PRERERANK_POOL_MAX`, `EXPLORATION_DISCOVERY_POST_RERANK_TOP_K` |

**Refinement vs expansion:** Analyzer/decision mapper sets `next_action`; engine applies gap-driven overrides (`_apply_gap_driven_decision`), refine cooldown (`_apply_refine_cooldown`), memory-based refine→expand coercion (~847–894), and oscillation detection (`_intent_oscillation_detected` — forces **expand** if last two refined intents match ~2239–2247).

---

## 5. Candidate flow (retrieval → merge → rerank → prune → next iteration)

**Note:** The code path is **not** “RRF → rerank”; it is **merge-by-file → sort → optional rerank → top‑k**.

| Stage | Drop / shrink condition |
|-------|---------------------------|
| Retrieval rows | Skips non-dict rows, rows without file path |
| Merge | One candidate per file |
| Prererank cap | `built[:EXPLORATION_DISCOVERY_PRERERANK_POOL_MAX]` (~1410) |
| `_may_enqueue*` | Drops files/symbols already explored, excluded, or already pending (~1416–1420, ~1553–1561) |
| Rerank | Skipped if disabled, reranker None, or below `RERANK_MIN_CANDIDATES` / `EXPLORATION_DISCOVERY_RERANK_MIN_CANDIDATES` (~1217–1224) |
| Post-rerank | `deduped = reranked[:EXPLORATION_DISCOVERY_POST_RERANK_TOP_K]` (~1422–1423) |
| `_enqueue_ranked` | Second `_may_enqueue` pass; scoper may shrink list; selector may return **`None`** (`no_relevant_candidate`) (~1522–1523) — **no targets enqueued** |
| Direction routing | `_enforce_direction_routing` can return **`[]`** if filtered buckets empty (~2161–2163) |

**Why count goes to zero:** No retrieval hits; all files filtered by `_may_enqueue`; rerank/selector returns no relevant candidate; or direction routing empties expansion. **Confirmed score bug** (§8A) also makes “low relevance” / retry logic think top score is always **0**, amplifying unnecessary refinements and unstable telemetry.

---

## 6. State management

| State | Persistence within run | Overwrite risk |
|-------|------------------------|----------------|
| `ex_state.seen_files`, `seen_symbols`, `explored_location_keys` | Accumulate | Refine **replaces** `intent` entirely (~1018–1020) — queries change, not seen sets |
| `ExplorationWorkingMemory` | In-memory; evidence keyed by `(file, symbol)` with merge rules | `ingest_discovery_candidates` adds discovery tier rows; analyzer adds/updates |
| Context feedback | Built fresh from memory + ex_state each retry/refine | Not a separate store — **no** disk persistence |
| `known_entities` in feedback | Union of memory evidence symbols/files and `ex_state` seen sets | Reflects run-so-far, not overwritten by a single field |

---

## 7. Failure handling and retry

- **Initial refinement:** Driven by `_classify_initial_refinement_reason` (~2426–2455) using candidate presence, **top_score vs threshold**, and intent shape heuristics.
- **Refine in loop:** `_refine_failure_reason` (~2106–2117) maps decision / streak to `low_relevance` or `insufficient_context`.
- **Does retry change behavior?** It **always** calls the LLM `QueryIntentParser.parse` with `failure_reason` and `context_feedback`. Deterministic behavior change is **not** guaranteed; `_remove_repeated_queries` prevents exact duplicate query strings from prior intent.

**Selector fallback:** If the batch selector JSON does not match candidates, `select_batch` **falls back to top‑`limit` in discovery order** (~275–283 `candidate_selector.py`) — except when `no_relevant_candidate` is explicitly true (returns `None`).

---

## 8. Root causes

### A. Confirmed issues (code references)

1. **Wrong attribute for discovery score (always reads 0)**  
   - **Files:** `agent_v2/exploration/exploration_engine_v2.py` (`_top_discovery_score`, ~2374–2380), `agent_v2/exploration/exploration_working_memory.py` (`ingest_discovery_candidates`, ~142–144)  
   - **Issue:** Code uses `getattr(c, "_discovery_max_score", 0.0)`. The schema field is **`discovery_max_score`** (`agent_v2/schemas/exploration.py` ~191). The private name does not exist on normal instances, so **top score and raw confidence from discovery are always 0**, falling back to minimum confidence in memory.  
   - **Effects:** `_classify_initial_refinement_reason` treats almost all non-empty candidate sets as **below relevance threshold** when `top_score < threshold` (~2446–2454); `_has_retry_improvement` rarely sees score improvement (~2384–2391); telemetry and retry decisions are systematically wrong.  
   - **Test artifact:** `tests/test_exploration_working_memory.py` ~73 uses `object.__setattr__(c, "_discovery_max_score", 0.9)` — reinforces the mistaken name instead of setting `discovery_max_score`.

2. **Narrative mismatch: “RRF”**  
   - Exploration discovery does not implement RRF; describe as merge + rerank + top‑k (see `_discovery` ~1255–1437).

### B. Potential issues (design hazards; verify in production traces)

1. **`_remove_repeated_queries` can strip the entire new intent** if the model outputs the same tokens as `previous_queries`, yielding empty discovery channels.  
2. **`_enforce_direction_routing` returning `[]`** silently drops all expansion targets when bucket metadata is present but keys do not match (~2161–2163).  
3. **Duplicate queue pops** do not increment `steps_taken`; termination relies on stagnation counter (3) — can look like “non-converging” spins in logs until stall.

### C. Missing safeguards

1. No explicit **maximum refine count** independent of backtracks (`EXPLORATION_MAX_BACKTRACKS` gates `_should_refine` but refine attempts still depend on decision path).  
2. No **monotonic** “best candidate score” tracker across inner loop iterations (only initial retry compares scores — and scores are wrong until §8A1 is fixed).  
3. No validation that **refined `QueryIntent` has at least one non-empty channel** before calling `_discovery` (empty intent still calls search with empty query lists → empty merge).

---

## 9. Fix plan (prioritized)

1. **P0 — Fix score field access:** Use `getattr(c, "discovery_max_score", None) or 0.0` (or `c.discovery_max_score`) in `_top_discovery_score` and `ingest_discovery_candidates`; align tests to set `discovery_max_score`. Re-run any exploration/telemetry tests.  
2. **P1 — Guard empty `QueryIntent` after `_remove_repeated_queries`:** Fallback to previous intent channel merge or skip refine discovery.  
3. **P1 — Logging:** After fix, validate `exploration.discovery` log line (~1425–1436) shows non-zero `merged_files` / scores where retriever returns scores.  
4. **P2 — Direction routing:** When `routed` is empty, log at **warning** with hint/bucket counts; optionally fall back to unfiltered `expanded` (~2143–2145 behavior).  
5. **P2 — Cap refine loops:** Document or enforce a hard max refines per run aligned with `EXPLORATION_MAX_BACKTRACKS` and product expectations.

---

## Output summary (requested format)

### Confirmed bugs

| File | Function | Issue |
|------|----------|--------|
| `agent_v2/exploration/exploration_engine_v2.py` | `_top_discovery_score` | Reads `_discovery_max_score`; real field is `discovery_max_score` → score 0 |
| `agent_v2/exploration/exploration_working_memory.py` | `ingest_discovery_candidates` | Same wrong attribute → discovery evidence confidence wrong |
| `tests/test_exploration_working_memory.py` | (test helper) | Sets `_discovery_max_score` via `__setattr__`, masking schema field |

### Root cause summary

Loop termination is **budget- and heuristic-driven**, not convergent. The primary **code defect** driving misleading “low relevance” and retry behavior is the **wrong discovery score attribute**, which zeros all score-based gating. Query “expansion” is largely **LLM-driven** with dedup stripping; candidates collapse when filters, selector `no_relevant_candidate`, or direction routing empty the lists.

### Fix plan

See §9 (P0–P2).

---

## Log hooks (validate in production)

- `exploration.discovery` — `_LOG.info` at ~1425 (`steps_taken`, budget counts, `merged_files`, `candidates_after_may_enqueue`, `post_rerank_top_k`).  
- `exploration.context_feedbacktrace` — `_LOG.info` at ~1686 with phase (`initial_query_retry`, `loop_refine`), counts, `failure_reason`.

---

*End of audit.*
