# SYSTEM AUDIT — COMPONENT TRACE (SEARCH → EDIT PIPELINE)

**Date:** 2025-03-23  
**Scope:** AutoStudio execution pipeline — SEARCH, SEARCH_CANDIDATES, BUILD_CONTEXT, EDIT

This document provides a complete, structured report of all components involved in the execution pipeline for controlled experiments and selective component disabling.

---

## SEARCH

### Components
- `agent/execution/step_dispatcher.py`: `dispatch()` — `Action.SEARCH` branch; calls `ExecutionPolicyEngine.execute_with_policy()`; on success calls `run_retrieval_pipeline()`; populates `state.context["search_target_candidates"]`, `candidates`, `query`; merges `candidates` into `raw["output"]`
- `agent/execution/step_dispatcher.py`: `_search_fn()` — cache → `hybrid_retrieve()` (if `ENABLE_HYBRID_RETRIEVAL`) → ordered retrievers (`retrieve_graph`, `retrieve_vector`, `retrieve_grep`, `list_dir`) → `search_code()` fallback → `file_search` directory listing fallback → `filter_and_rank_search_results()`; optional `trace_stage(..., "retrieval")`
- `agent/execution/step_dispatcher.py`: `_get_retrieval_order()`, `_get_retrieval_cache_size()`
- `agent/retrieval/repo_map_lookup.py`, `agent/retrieval/anchor_detector.py`
- `agent/retrieval/retrieval_cache.py`: `get_cached` / `set_cached`
- `agent/retrieval/search_pipeline.py`: `hybrid_retrieve()`
- `agent/retrieval/graph_retriever.py`: `retrieve_symbol_context()`
- `agent/retrieval/vector_retriever.py`: `search_by_embedding(..., top_k=5)`
- `agent/tools/serena_adapter.py` (via imports): `search_code()`
- `agent/tools/list_files.py`: `list_files()`
- `agent/retrieval/result_contract.py`: `normalize_result()`
- `agent/retrieval/search_target_filter.py`: `filter_and_rank_search_results()`
- `agent/execution/policy_engine.py`: `ExecutionPolicyEngine._execute_search()`
- `agent/execution/mutation_strategies.py`: `get_initial_search_variants()`, `generate_query_variants()`
- `agent/retrieval/rewrite_query_with_context`: LLM rewrite path
- `agent/retrieval/retrieval_pipeline.py`: `run_retrieval_pipeline()`

### Inputs / Outputs
- **In:** Step dict (`action="SEARCH"`, `description` or `query`, `artifact_mode` must be `"code"`)
- **In:** `AgentState.context` (`project_root`, `trace_id`, `chosen_tool`, `parent_instruction`, etc.)
- **Out (success):** `{ success=True, output: { results, query, candidates, attempt_history, search_quality } }`
- **Out (state):** `ranked_context`, `search_memory`, `files`, `snippets`, `search_target_candidates` populated

### Transformations
- Query repair when < 2 tokens (replace with instruction slice)
- Repo map probe; hybrid retrieval may replace raw result
- Retriever loop: graph → vector (top_k=5) → grep; `search_code` fallback
- Empty results → `file_search` (up to 10 entries)
- `filter_and_rank_search_results`: path filtering, scoring, caps
- Policy: multi-query fanout (deterministic variants + rewriter)
- Post-success: full `run_retrieval_pipeline`

### Loss Points
- Retriever empties at any stage
- `file_search` / `list_dir` treated as **invalid** for success → triggers retry/rewrite
- `filter_and_rank_search_results`: drops index paths, dirs, out-of-root; downranks tests; may drop `.md` without docs alignment
- Snippet empty: non-`.py` with empty snippet can invalidate hit
- `raw_results[:MAX_SEARCH_RESULTS]` cap in pipeline
- Policy exhaustion → `"all search attempts returned empty results"`

### Control Logic
- `normalize_action_for_execution`: `SEARCH_CANDIDATES` + code → `SEARCH` before policy
- SEARCH policy: up to 5 attempts × multiple queries per attempt; stops on first valid result
- `artifact_mode=docs` cannot use SEARCH (lane violation)

### Config
- `POLICIES["SEARCH"]`: `max_attempts: 5`, `retry_on: ["empty_results"]`
- `_MAX_REWRITE_QUERIES_PER_SEARCH_ATTEMPT = 5`
- `get_initial_search_variants(..., max_total=3)`
- `config/retrieval_config.py`: `ENABLE_HYBRID_RETRIEVAL`, `ENABLE_VECTOR_SEARCH`, `RETRIEVAL_CACHE_SIZE`, `MAX_SEARCH_RESULTS`, `FALLBACK_TOP_N`

---

## SEARCH_CANDIDATES

### Components
- `agent/execution/step_dispatcher.py`: `dispatch()` branch `action == SEARCH_CANDIDATES` — up to 3 attempts; grep fallback for code mode
- `agent/tools/search_candidates.py`: `search_candidates()` → `search_candidates_with_mode()` — docs: `agent/retrieval/docs_retriever.search_docs_candidates_with_stats`; code: `agent/retrieval/retrieval_pipeline.search_candidates()`
- `agent/retrieval/retrieval_pipeline.py`: `search_candidates()` — parallel BM25, vector, grep, repo_map → RRF → optional `_filter_by_service_dirs` → top 20

### Inputs / Outputs
- **In:** `query` from step; `state`; `artifact_mode` (code|docs)
- **Out:** `{ success: True, output: { candidates: [...], fallback?: "grep"|"none" } }`
- **State:** `state.context["candidates"]`, `state.context["query"]`

### Transformations
- Code: query expansions, parallel source merge, RRF, normalized scores, service-dir filter, **snippet truncated to 500 chars**
- Docs: filesystem scan in `docs_retriever`
- Fallback: grep hits mapped to `{symbol, file, snippet, score: 0.5, source: "grep"}`

### Loss Points
- `SEARCH_CANDIDATES_TOP_K = 20` — only 20 merged candidates returned
- Snippet `[:500]` per candidate
- Service dirs filter may drop candidates
- Test path downweight
- Empty after 3 attempts → `candidates: []`, `fallback: "none"`

### Control Logic
- Retry loop: `for attempt in range(3)`
- Docs lane: no grep fallback

### Config
- `SEARCH_CANDIDATES_TOP_K = 20`
- `BM25_TOP_K`, `ENABLE_BM25_SEARCH`, `ENABLE_VECTOR_SEARCH`, `RETRIEVAL_USE_SERVICE_DIRS`, `RETRIEVAL_TEST_DOWNWEIGHT`

---

## BUILD_CONTEXT

### Components
- `agent/execution/step_dispatcher.py`: `BUILD_CONTEXT` — **code: no-op** (`"context_ready"`); **docs:** `agent/tools/build_context.build_context(..., artifact_mode="docs")` → `docs_retriever.build_docs_context`
- `agent/tools/build_context.py` (code with candidates): `run_retrieval_pipeline(search_results, state, query)`; cache key from top 5 candidates
- Primary builder (invoked by SEARCH success): `agent/retrieval/retrieval_pipeline.run_retrieval_pipeline()`

### Inputs / Outputs
- Dispatcher no-op (code): output `"context_ready"`
- `build_context` (code): `candidates` or `state.context["candidates"]`; `state.context["query"]` or `instruction`
- `run_retrieval_pipeline`: mutates `state.context`; sets `ranked_context`, `retrieval_candidate_pool`, `search_memory`, etc.

### Transformations (run_retrieval_pipeline)
- `filter_and_rank_search_results`; `coerce_snippet_text`
- `detect_anchors`; fallback `results[:FALLBACK_TOP_N]`
- Optional `localize_issue`
- Graph: `expand_from_anchors`, `expand_search_results`, `read_symbol_body` / `read_file` / `expand_region_bounded` / `expand_file_header`, `find_referencing_symbols`
- `build_context_from_symbols` → `retrieved_*`, `context_snippets`
- `_attach_relationship_links` (caps `MAX_RELATIONS_PER_ROW`, `MAX_RELATIONS_TOTAL`)
- `classify_query_intent`, `apply_intent_bias`; sort by `selection_score`; slice `[:MAX_RETRIEVAL_RESULTS]`
- `deduplicate_candidates`
- Slice `[:MAX_RERANK_CANDIDATES]` pre-rerank
- Optional reranker → `prune_context(max_snippets=MAX_CONTEXT_SNIPPETS, max_chars=DEFAULT_MAX_CHARS)`
- Fallback: retriever-score sort + same `prune_context`
- Optional `compress_context` if `repo_summary` set
- `_inject_instruction_path_snippets` (up to 12000 chars per injected path when docs/code alignment)
- `state.context["ranked_context"] = final_context`

### Loss Points
- `raw_results[:MAX_SEARCH_RESULTS]` cap
- No anchors / empty → `_maybe_seed_ranked_context_when_search_empty` or empty `ranked_context`
- Dedupe may remove rows (error log if all impl-body rows removed)
- Rerank budget `MAX_RERANK_CANDIDATES`
- **`prune_context`:** max 6 snippets, 8000 chars; truncates snippets; skips non-impl rows when `remaining < 80` (except minimal 40 chars for `implementation_body_present`)
- Optional compression token cap

### Control Logic
- Code BUILD_CONTEXT: **no-op** in dispatcher — pipeline runs via SEARCH success, not BUILD_CONTEXT
- Cache hit in `build_context` tool skips pipeline

### Config
- `MAX_CONTEXT_SNIPPETS` (default 6), `DEFAULT_MAX_CHARS` (8000), `MAX_SEARCH_RESULTS`, `MAX_RETRIEVAL_RESULTS`, `MAX_RERANK_CANDIDATES`
- `MIN_FALLBACK_CHARS = 40` in context_pruner

---

## EDIT

### Components
- `agent/execution/step_dispatcher.py`: `EDIT` — `build_edit_binding(state)`; `ExecutionPolicyEngine.execute_with_policy` → `_edit_fn`
- `agent/execution/edit_binding.py`: `build_edit_binding` — first row of `ranked_context`; evidence = first 300 chars of snippet/content
- `agent/execution/policy_engine.py`: `_execute_edit` — `symbol_retry(step, state)` → up to 2 variants; each calls `_edit_fn`
- `agent/execution/step_dispatcher.py`: `_edit_fn` — `plan_diff`, `resolve_conflicts`, `run_edit_test_fix_loop`; path validation; `MAX_FILES_EDITED`, `MAX_PATCH_SIZE`
- `agent/runtime/execution_loop.py`: `_run_loop` — `plan_diff`, `to_structured_patches`, `validate_syntax_plan`, `verify_patch_plan`, `execute_patch`, `run_tests`; semantic feedback; `check_structural_improvement`; stagnation / `MAX_SEMANTIC_RETRIES` / `MAX_STAGNATION`
- `editing/syntax_validation.py`: `validate_syntax_plan` — `apply_patch_in_memory`; returns `patch_apply_failed` when apply fails
- `editing/patch_verification.py`: `verify_patch` / `verify_patch_plan` — checks `has_effect`, `targets_correct_file`, `is_local`
- `editing/patch_executor.py`: `execute_patch` — text_sub / AST apply; `target_not_found` when old not in src
- `editing/semantic_feedback.py`: `check_structural_improvement`, `patch_signature`
- `agent/execution/mutation_strategies.py`: `symbol_retry` — variants with file-level, symbol-short, **alternate target from ranked_context**

### Inputs / Outputs
- **In:** Step `description`; optional `edit_target_file_override`, `edit_target_level`, `edit_target_symbol_short`
- **In:** `state.context`: `ranked_context`, `prior_phase_ranked_context`, `search_target_candidates`, `retrieved_symbols`, `edit_binding`, `failure_state`, snapshots
- **Out (success):** `{ files_modified, patches_applied, planned_changes }`
- **Out (failure):** `failure_reason_code`, `error`, `reason`, `attempt_history`

### Transformations
- `plan_diff` consumes context (including ranked context; proposal limits in editing_config)
- `to_structured_patches` normalizes planner output
- `resolve_conflicts` may split into sequential groups
- Syntax: `apply_patch_in_memory` → `patch_apply_failed` when apply returns `None`
- Verification: `verify_patch` compares patch file to `edit_binding["file"]`; checks old in file for text_sub
- Execute: text_sub / AST; `old not in src` → `target_not_found`

### Loss Points
- **Binding:** Only first `ranked_context[0]` → if ranking/prune put wrong file first, chosen target drifts
- **Evidence truncation:** 300 chars max
- Empty `plan_diff` changes → `empty_patch` / `no_changes`
- `plan_diff` proposal limits (EDIT_PROPOSAL_*)

### Control Logic
- Policy: `symbol_retry` variants (file level, symbol short, **alternate file from ranked_context/files**)
- Inner loop: `MAX_EDIT_ATTEMPTS` (default 3); `MAX_SEMANTIC_RETRIES` (2); `MAX_STAGNATION`
- Structural gate: `check_structural_improvement` **before** apply; **`no_progress_repeat`** if patch signature in `attempted_patches`

### Config
- `config/agent_runtime.py`: `MAX_EDIT_ATTEMPTS`, `MAX_PATCH_LINES`, `MAX_PATCH_FILES`, `MAX_SEMANTIC_RETRIES`, `MAX_STAGNATION`, `MAX_SAME_ERROR_RETRIES`, `TEST_TIMEOUT`
- `config/editing_config.py`: `MAX_PATCH_SIZE`, `MAX_FILES_EDITED`, `EDIT_PROPOSAL_MAX_CONTENT`, `EDIT_PROPOSAL_EVIDENCE_MAX`, `EDIT_PROPOSAL_SYMBOL_BLOCK_MAX`, `SEMANTIC_FEEDBACK_MAX_SUMMARY`
- `editing/patch_executor.py`: `MAX_FILES_PER_EDIT = 5`, `MAX_PATCH_LINES = 200`

---

## SPECIAL FOCUS

### 1. Where context is REDUCED or LOST
- `context_pruner.prune_context`: snippet truncation; max snippets; skips non-impl rows when `remaining < 80` (except minimal 40 chars for impl)
- Pipeline caps: `MAX_SEARCH_RESULTS`, `MAX_RETRIEVAL_RESULTS`, `MAX_RERANK_CANDIDATES`; rerank top-K; dedupe; optional `compress_context`
- `edit_binding` evidence: 300 chars only
- `plan_diff` / proposal limits: `EDIT_PROPOSAL_*` truncate content fed to patch planning
- EXPLAIN path: `assemble_reasoning_context(..., max_chars=8000)` (affects EXPLAIN only)

### 2. Where model input is MODIFIED before EDIT
- Retrieval pipeline + prune + optional compression **before** `ranked_context` consumed by `build_edit_binding` and `plan_diff`
- `symbol_retry` injects **`edit_target_file_override`** from alternate ranked/file list
- Execution loop appends **semantic / causal feedback** into instruction on retries

### 3. Where EDIT output is VALIDATED or REJECTED
- `syntax_validation.validate_syntax_plan` → `error_type: patch_apply_failed` when in-memory apply returns `None`
- `patch_verification.verify_patch` → `no_meaningful_diff`, `targets_wrong_file`, `target_not_found`
- `patch_executor.execute_patch` → `target_not_found`, `invalid_patch_syntax`, effectiveness rejections
- `semantic_feedback.check_structural_improvement` → `wrong_target_file`, `wrong_target_symbol`, `patch_unchanged_repeat`, **`no_progress_repeat`**
- Execution loop: patch size limits (`MAX_PATCH_FILES`, `MAX_PATCH_LINES`)

### 4. Origins of failure reason codes

| Code | Origin | Condition |
|------|--------|-----------|
| **`wrong_target_file`** | `editing/semantic_feedback.check_structural_improvement` | New patch `file` (normalized) ≠ `binding["file"]` when both set |
| **`patch_apply_failed`** | `editing/syntax_validation.validate_syntax` / `validate_syntax_plan` | `apply_patch_in_memory` returns `None` (text_sub old not in content, or AST apply raised) |
| **`no_progress_repeat`** | `editing/semantic_feedback.check_structural_improvement` | `patch_signature(new_patch)` ∈ `failure_state["attempted_patches"]` |
| **`targets_wrong_file`** | `editing/patch_verification.verify_patch` | Resolved proposal path ≠ resolved `edit_binding` file |

---

## Cross-Reference Summary

| Stage | Key components | Key loss points | Key configs |
|-------|----------------|-----------------|-------------|
| **SEARCH** | `_search_fn`, `_execute_search`, `run_retrieval_pipeline`, `search_target_filter` | Invalid fallbacks; filter drops; `MAX_SEARCH_RESULTS`; exhaustion | `max_attempts=5`, rewriter cap 5, initial variants 3 |
| **SEARCH_CANDIDATES** | `search_candidates` tool, `retrieval_pipeline.search_candidates`, grep fallback | Top-20; service-dir filter; 3-attempt empty | `SEARCH_CANDIDATES_TOP_K=20` |
| **BUILD_CONTEXT** | No-op (code); `run_retrieval_pipeline`; `prune_context`; optional `compress_context` | Dedupe; rerank cap; prune char/snippet limits | `MAX_CONTEXT_SNIPPETS`, `DEFAULT_MAX_CHARS`, reranker |
| **EDIT** | `build_edit_binding`, `plan_diff`, `run_edit_test_fix_loop`, syntax/verify/execute, `check_structural_improvement` | Binding uses only first ranked row; evidence 300; proposal caps | `MAX_EDIT_ATTEMPTS`, `MAX_PATCH_*`, `EDIT_PROPOSAL_*` |
