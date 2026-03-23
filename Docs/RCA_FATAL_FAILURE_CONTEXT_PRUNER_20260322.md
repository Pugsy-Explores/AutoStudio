# RCA: FATAL_FAILURE + context_pruner "char budget skips row" in Live Mode

**Task:** core12_pin_requests_explain_trace (paired8, explain_artifact)  
**Run:** 20260322_192725_e2130b  
**Observed logs:**
1. `[execution_loop] FATAL_FAILURE, stopping (step_id=2 action=EDIT)`
2. `[context_pruner] char budget skips row (file=.../sessions.py); trying smaller rows`

---

## Executive Summary

The EDIT step fails because the policy engine exhausts retries and returns `"edit failed after retries"`. That result is classified as **FATAL_FAILURE** by `classify_result()`, so the orchestrator stops immediately. The root cause is that the **context pruner drops `sessions.py`** when the character budget is nearly exhausted, leaving the EDIT step with insufficient context to ground its explanation. The model produces weak or failed edits; policy retries all fail; the loop terminates with FATAL_FAILURE.

---

## 1. FATAL_FAILURE Flow

### 1.1 Where it happens

- **File:** `agent/orchestrator/execution_loop.py` ~L288–303
- **Trigger:** `classification == ResultClassification.FATAL_FAILURE.value`
- **Effect:** Loop breaks immediately; no step retry, no replan

```python
if classification == ResultClassification.FATAL_FAILURE.value:
    logger.warning(
        "[execution_loop] FATAL_FAILURE, stopping (step_id=%s action=%s)",
        step_id, action,
    )
    break
```

### 1.2 Classification source

- **File:** `agent/execution/policy_engine.py`
- **Function:** `classify_result(action, result)`

FATAL_FAILURE is returned when:

1. **Exhausted retries** (L166–168):
   - `"exhausted" in error` or `"after retries" in error`
2. **Attempt history exhausted** (L169–175):
   - `attempt_history` present and `len(history) >= max_attempts` for that action

### 1.3 EDIT step: "edit failed after retries"

- **File:** `agent/execution/policy_engine.py` ~L531–539
- When all EDIT attempts fail, the policy engine returns:

```python
return _with_classification(
    {
        "success": False,
        "output": {"attempt_history": attempt_history},
        "error": "edit failed after retries",
    },
    "EDIT",
)
```

The string `"after retries"` matches `classify_result()` → FATAL_FAILURE.

### 1.4 EDIT policy

- **File:** `agent/execution/policy_engine.py` ~L119
- `POLICIES["EDIT"]`: `{"max_attempts": 3, "mutation": "symbol_retry", "retry_on": ["symbol_not_found"]}`
- `_execute_edit` uses `symbol_retry(step, state)[:max_attempts]`; if all attempts fail, it returns `"edit failed after retries"`.

---

## 2. context_pruner "char budget skips row"

### 2.1 Logic

- **File:** `agent/retrieval/context_pruner.py` ~L54–62
- **Config:** `DEFAULT_MAX_CHARS=8000`, `MAX_CONTEXT_SNIPPETS=6` (`retrieval_config.py`)

```python
remaining = max_chars - total_chars
if snip_len > remaining:
    if remaining < 80:
        if c.get("implementation_body_present") is True:
            logger.warning(
                "[context_pruner] char budget skips row (file=%s); trying smaller rows",
                c.get("file"),
            )
        continue
    snippet = snippet[:remaining]  # else: truncate
```

- When `remaining < 80` and the row has `implementation_body_present=True`, the row is **skipped entirely** (no truncation).
- Otherwise the snippet is truncated to fit.

### 2.2 Why `sessions.py` is skipped

- `sessions.py` in requests is ~834 lines and yields large snippets.
- After earlier ranked rows, the pruner can hit `remaining < 80`.
- If the row for `sessions.py` has `implementation_body_present=True`, it is skipped.

### 2.3 When prune_context runs

- **File:** `agent/retrieval/retrieval_pipeline.py` ~L1015–1018, ~L1059–1062
- Called after reranking (or retriever-score fallback) to build `final_context` for the step.
- Used for both SEARCH (context for reasoning) and EDIT (context for patch generation).

---

## 3. Causal Chain: pruner → EDIT failure → FATAL_FAILURE

```
1. core12_pin_requests_explain_trace requires:
   - TRACE_NOTE.md
   - src/requests/sessions.py (Session.request, hooks, redirect path)

2. Retrieval returns ranked context including sessions.py.

3. Context pruner iterates over ranked rows:
   - Earlier snippets consume most of the 8000-char budget.
   - When sessions.py is reached, remaining < 80.
   - Row has implementation_body_present=True → SKIP (no truncation).
   - Log: "char budget skips row (file=.../sessions.py); trying smaller rows"

4. Final context sent to EDIT lacks sessions.py content.

5. EDIT step (write explain_out.txt):
   - Model has no Session.request/hooks/redirect context.
   - Produces weakly grounded or empty output; edit fails.

6. Policy engine retries EDIT (symbol_retry) up to max_attempts.
   - Same missing context → all attempts fail.

7. Policy returns: success=False, error="edit failed after retries".

8. classify_result("EDIT", result) → FATAL_FAILURE (match on "after retries").

9. execution_loop sees FATAL_FAILURE → stops.
```

---

## 4. Code References

| Component        | File                                      | Lines / Notes                          |
|-----------------|-------------------------------------------|----------------------------------------|
| FATAL stop      | agent/orchestrator/execution_loop.py      | 288–303                                |
| classify_result | agent/execution/policy_engine.py          | 154–198 (exhausted/after retries)      |
| EDIT exhausted  | agent/execution/policy_engine.py          | 531–539                                |
| prune_context   | agent/retrieval/context_pruner.py         | 54–62 (skip when remaining < 80)       |
| Char/snippet    | config/retrieval_config.py                | DEFAULT_MAX_CHARS=8000, 6 snippets     |
| Task spec       | tests/agent_eval/suites/core12.py         | 110–127 (core12_pin_requests_explain_trace) |

---

## 5. Recommendations

### 5.1 Short-term

1. **Increase DEFAULT_MAX_CHARS** (e.g. 8000 → 12000) for explain_artifact tasks so key files like `sessions.py` fit.
2. **Adjust skip behavior** when `remaining < 80`: instead of fully skipping implementation rows, add a truncated slice (e.g. first 60 chars) to preserve at least file presence.

### 5.2 Medium-term

1. **Explain-aware context** for explain_artifact tasks: reserve budget for explicitly required files (e.g. from instruction) before filling with other snippets.
2. **Prefer required files** in ranking when the task references them (e.g. sessions.py, TRACE_NOTE.md).

### 5.3 Longer-term

1. **Retrieval quality checks** before EDIT: detect when required files are missing and trigger query rewrite or expanded retrieval instead of proceeding with weak context.
2. **Relax FATAL for exhausted EDIT** in explain-only flows: treat as RETRYABLE and allow replan with expanded retrieval when the failure appears context-related.

---

## 6. Verification

To confirm the causal chain:

1. Inspect run trace for `20260322_192725_e2130b`:
   - `context_pruner` log before EDIT.
   - Contents of `ranked_context` / `final_context` for the EDIT step.
2. Check if `sessions.py` is absent from `final_context` for the failing EDIT.
3. Compare `attempt_history` for EDIT: repeated `symbol_not_found` or other context-related errors.
