# Stage 42 — Bounded Deterministic Query-Variant Closeout

## Summary

Stage 42 adds bounded deterministic query-variant generation before the first SEARCH retrieval attempt. On attempt 1, the policy engine now tries up to 3 sequential queries (base + up to 2 identifier-style variants) without calling the LLM rewriter. Attempts 2+ are unchanged. No backend changes, no planner changes, no retry-count increase.

---

## 1. What Changed

| File | Symbol | Change |
|------|--------|--------|
| `agent/execution/mutation_strategies.py` | `get_initial_search_variants` | New public helper: `[base] + up to 2 variants` from `generate_query_variants`, deduped, hard-capped at `max_total`, base first. |
| `agent/execution/policy_engine.py` | `_execute_search` + import | Attempt 1: computes `initial = get_initial_search_variants(retrieval_input, 3)`; if non-empty, uses as `queries_to_try` and **skips LLM rewriter**. Attempts 2+ unchanged. |
| `tests/test_policy_engine.py` | `TestGetInitialSearchVariants`, new engine tests | Tests for helper contract; tests for attempt 1 no-rewriter behavior; short-circuit on first success; rewriter runs on attempt 2+ when variants fail. |

---

## 2. Behavior After Stage 42

```
SEARCH step
  → retrieval_input = step.get("query") or step.get("description")
  → attempt 1:
      initial = get_initial_search_variants(retrieval_input, max_total=3)
      if non-empty: try each query sequentially, stop on first success
      if empty: fall through to rewriter/fallback
  → attempts 2+:
      rewrite_query_fn(retrieval_input, user_request, attempt_history)
      try each returned query sequentially, stop on first success
```

**Execution order on attempt 1:** base first, then variant 1, then variant 2. Early exit. No LLM call.

**Backward compat:**
- Steps with no `query` field use `description` as `retrieval_input` — identical to pre-Stage 42.
- When helper returns `[base]` only (single-token or no variants), behavior is identical to before.
- Rewriter still used for attempts 2–5. No retry-count change.

---

## 3. `get_initial_search_variants` Contract

| Property | Value |
|----------|-------|
| Base always first | Yes |
| Hard cap | `max_total` (default 3) |
| Dedupe | By exact string |
| Blank/empty base | Returns `[]` |
| Algorithm | Wraps `generate_query_variants`; no typo/fuzzy logic |
| Deterministic | Yes |

---

## 4. What Stage 42 Does Not Fix

- **Typo tolerance:** None. Misspelled identifiers still miss.
- **Repo-map exactness:** No change. Case-insensitive substring match, no edit distance.
- **snake_case / camelCase / path normalization:** No change at this stage.
- **Unbounded LLM query list on attempts 2+:** No cap added. Still risk of rewrite explosion if LLM returns a long `queries` list.
- **Fake-success fallback from `list_files`:** Not addressed. File-listing "success" on empty SEARCH still propagates.
- **Candidate ranking:** No change.

---

## 5. Pre-Stage 42 Tests Updated

- `test_search_retries_then_succeeds_on_third_query`: adjusted to expect rewriter not called on attempt 1 (3 deterministic searches run first without rewriter).
- `test_search_rewriter_receives_planner_step_user_request_and_attempt_history`: adjusted to expect 3 failed variant searches before rewriter activates (attempt_history_len offset by 3).

---

## 6. Next Audit Questions (Not In Scope Here)

The following were identified as follow-up audit targets after Stage 42:
- Cap/sanitize LLM rewrite query lists on attempts 2+ (unbounded `queries` field).
- Fake-success from `list_files` fallback in `_search_fn`.
- Repo-map lookup quality for typo/misspell cases.

These are deferred to a subsequent stage audit.

---

## 7. Test Coverage

```
tests/test_policy_engine.py:
  TestGetInitialSearchVariants
    test_returns_base_first_and_cap_three
    test_hard_cap
    test_dedupe_no_duplicate_strings
    test_empty_base_returns_empty
    test_max_total_one_returns_only_base

  TestExecutionPolicyEngineSearch
    test_attempt1_uses_deterministic_variants_no_rewriter_until_success
    test_attempt1_first_variant_succeeds_short_circuits
    test_attempt2_uses_rewriter_when_attempt1_variants_exhausted
    (updated) test_search_retries_then_succeeds_on_third_query
    (updated) test_search_rewriter_receives_planner_step_user_request_and_attempt_history
```

All 25 policy-engine tests pass.
