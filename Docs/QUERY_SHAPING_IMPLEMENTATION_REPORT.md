# Query Shaping Implementation Report — First EXPLAIN Retrieval

## 1. CHANGE MADE

**Files changed:**
- `agent/execution/step_dispatcher.py`

**Helper added:**
- `_shape_query_for_explain_retrieval(instruction: str) -> str | None`
  - Deterministic extraction of code-explanation target from compound requests
  - Returns focused query (e.g. `"replanner"`) or `None` when no target extractable

**Where shaped query is used:**
- First EXPLAIN retrieval path in `dispatch()`: when `has_context` is False (no prior `ranked_context`), `artifact_mode == "code"`, and we are about to call `_search_fn()`, the query is shaped before the search call.

**Integration point (step_dispatcher.py lines ~508–515):**
```python
else:
    # Query shaping: use focused code-explanation target for first EXPLAIN retrieval
    # to avoid mixed context from broad compound instructions (replan_count 2->1).
    if artifact_mode == "code":
        shaped = _shape_query_for_explain_retrieval(query)
        if shaped:
            query = shaped
    search_output = _search_fn(query, state)
```

---

## 2. QUERY-SHAPING RULE

**Rule:** When the instruction matches one of the patterns below, extract the first symbol-like token as the retrieval query. Otherwise return `None` and fall back to the original instruction.

**Patterns (in order):**
1. `explain how X ...` → extract `X`
2. `explain X ...` → extract first non-generic token from `X` (e.g. `replanner` from `replanner flow`)
3. `how X works` → extract `X`
4. `X flow` (standalone) → extract `X`

**Generic words (skipped):** `flow`, `architecture`, `docs`, `documentation`, `work`, `works`, `the`, `a`, `an`, `how`

**Examples:**

| Input instruction | Shaped query |
|-------------------|--------------|
| `show architecture docs and explain replanner flow` | `replanner` |
| `explain how replanner preserves dominant lane` | `replanner` |
| `explain the plan_resolver` | `plan_resolver` |

**No shaping applied:**

| Input instruction | Shaped query |
|-------------------|--------------|
| `where is StepExecutor implemented` | `None` (unchanged) |
| `list all files in the project` | `None` (unchanged) |

---

## 3. TESTS ADDED

**File:** `tests/test_explain_query_shaping.py`

**Tests:**
- `test_compound_explain_instruction_shaped` — compound instruction → `replanner`
- `test_explain_how_x_shaped` — `explain how X ...` → `replanner`
- `test_explain_target_flow_shaped` — `explain X flow` → `replanner`
- `test_explain_target_only_shaped` — `explain the plan_resolver` → `plan_resolver`
- `test_simple_code_search_not_shaped` — `where is StepExecutor implemented` → `None`
- `test_symbol_lookup_not_shaped` — `find retrieve_graph` → `None`
- `test_fallback_when_no_target_extractable` — `list all files` → `None`
- `test_how_x_works_shaped` — `how replanner works` → `replanner`
- `test_empty_or_invalid_returns_none` — empty, whitespace, `None` → `None`

**Result:** All 9 tests pass.

---

## 4. SCENARIO VERIFICATION

| Scenario | status | replan_count | final_reason_code | first retrieval query | first EXPLAIN succeeded? |
|----------|--------|--------------|-------------------|------------------------|---------------------------|
| **5** | passed | 1 | null | `replanner` | No (refused; 1 replan) |
| **6** | passed | 0 | null | `replanner` | Yes |
| **4** | failed | 0 | goal_not_satisfied | N/A (CODE_SEARCH only) | N/A |

**Notes:**
- S5: First retrieval used `replanner` (shaped). Model refused on first attempt; one replan; successful on second EXPLAIN.
- S6: First retrieval used `replanner` (shaped). Model produced partial answer on first attempt; no replans.
- S4: No regression. CODE_SEARCH only; plan exhausted after SEARCH without EXPLAIN. Failure is pre-existing per FIX_A verification.

---

## SUCCESS CRITERIA

| Criterion | Result |
|-----------|--------|
| Scenario 5: passed, replan_count = 1 | Yes |
| Scenario 6: passed, replan_count = 1 | Yes (replan_count = 0) |
| Scenario 4: no regression | Yes |
| No changes to Fix A behavior | Yes |
| No changes to validator/replanner/stall logic | Yes |
