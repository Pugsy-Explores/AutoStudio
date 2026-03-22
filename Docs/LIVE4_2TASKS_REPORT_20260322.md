# Live4 Run Report — 2 Tasks, Deep Log Analysis

**Date:** 2026-03-22  
**Suite:** live4 (2 tasks: core12_mini_repair_calc, core12_mini_repair_parse)  
**Execution mode:** live_model  
**Task timeout:** 120s  
**Duration:** 93.25s  
**Run directory:** `artifacts/agent_eval_runs/20260322_153943_36cb3f`  
**Log file:** `docs/live4_run_2tasks_20260322_153942.txt`

---

## Executive Summary

| Metric | Value |
|--------|-------|
| **Tasks run** | 2 |
| **success_count** | 2 (validation passed) |
| **structural_success_count** | 0 |
| **patches_applied_total** | 0 |
| **Model calls** | 22 total (6 small, 16 reasoning) |

**Both tasks failed to apply patches.** Tasks are graded as "success" (validation_passed=true) because tests pass, but 0 patches were applied. Root cause: **STATE_INCONSISTENCY** (calc) and **no_effect_change** (parse).

---

## Task 1: core12_mini_repair_calc

**Instruction:** Repair src/calc/ops.py so multiply(2, 3) == 6 and tests pass.

### Failure: `target_not_found` (text_sub old snippet not found)

**Patch grounding audit (from outcome.json):**

```json
{
  "failure_type": "STATE_INCONSISTENCY",
  "reason": "target_not_found",
  "file_contains_old_snippet": false,
  "old_snippet": "return a * b + 1",
  "evidence_span": "from __future__ import annotations\n\n\ndef multiply(a: int, b: int) -> int:\n    # intentional bug for benchmark (2*3 should be 6)\n    return a * b + 1",
  "snippet_match": true,
  "locality": "valid"
}
```

**Classification:** **STATE_INCONSISTENCY** — file state changed between proposal and patch execution.

### Root Cause (log analysis)

1. **First edit proposal:** Full file content had `return a * b + 1` (buggy). Model produced correct patch: `old: "return a * b + 1"`, `new: "return a * b"`. Patch should have applied.

2. **Retry cycle:** After first failure, critic diagnosed "retrieval_miss". Second edit proposal showed:
   - **Relevant context (evidence):** `return a * b + 1`
   - **Full file content:** `return a * b` (already fixed!)

3. **Evidence vs file mismatch:** The model was given **conflicting inputs** — evidence (from ranked_context/retrieval) showed the buggy code, while full_content (read from disk) showed the fixed code. The model followed evidence and produced `old: "return a * b + 1"`. The on-disk file already had `return a * b` → patch failed with "text_sub old snippet not found".

4. **Hypothesis:** Either (a) a prior patch or synthetic repair applied successfully, changing the file before the model-generated patch ran; or (b) resolve_conflicts / patch ordering applied a fix first; or (c) full_content and evidence are sourced from different moments in time (evidence stale from retrieval, full_content fresh from disk).

### Critic misdiagnosis

Critic returned `failure_type: "retrieval_miss"` with evidence "text_sub old snippet not found". The patch grounding audit correctly identifies this as **STATE_INCONSISTENCY**, not retrieval_miss. Retrieval found the correct file; the failure was stale file state.

---

## Task 2: core12_mini_repair_parse

**Instruction:** Fix tokenize() in src/parse/split.py to split on whitespace so test_tokenize_words passes.

### Failure: `no_effect_change`

**Log evidence (line 1160–1163):**

```
Full file content:
def tokenize(line: str) -> list[str]:
    return line.split()   # already correct

Model produced:
{ "action": "text_sub", "old": "return line.split()", "new": "return line.split()" }
```

**Root cause:** The file already had `return line.split()` (correct). The model produced a **no-op patch** (old == new). The "Relevant context" showed `return [line]` (buggy), but "Full file content" showed `return line.split()`. The model chose to use full_content for the old snippet, producing a no-op.

---

## Patch Reject Histogram

| Reason | Count |
|--------|-------|
| target_not_found | 1 |
| no_effect_change | 1 |

---

## Model Call Breakdown

| Stage | Count |
|-------|-------|
| Intent routing | 2 |
| Planner | 2 |
| Edit proposal | 6+ |
| Critic | 2 |
| Retry planner | 2 |
| **Total** | 22 |

---

## Patch Grounding Audit — Findings

| Task | failure_type | snippet_match | locality | file_contains_old_snippet |
|------|--------------|---------------|----------|---------------------------|
| core12_mini_repair_calc | STATE_INCONSISTENCY | true | valid | false |
| core12_mini_repair_parse | (no patch_debug for no_effect) | — | — | — |

The calc task audit confirms: patch was correctly grounded to evidence (snippet_match=true, locality=valid). Failure was due to file state not containing the expected old snippet at apply time.

---

## Recommendations

1. **Ensure evidence and full_content consistency:** When building the edit proposal, use the same file snapshot for both evidence (edit_binding) and full_content. If evidence comes from ranked_context (retrieval), re-read or reconcile so it matches the file at proposal time.

2. **Re-read file before retry:** On patch failure, refresh full_content from disk before calling the model again. Avoid mixing stale evidence with fresh file reads.

3. **Detect no-op patches:** Before applying, reject patches where old == new (no-op). Either skip or prompt the model to produce a non-no-op when the file is already correct.

4. **Critic hint:** Consider passing `patch_validation_debug.failure_type` (STATE_INCONSISTENCY vs GENERATION_CONTRACT_MISMATCH) to the critic so it can suggest more accurate retry strategies (e.g. re-read file vs rewrite query).

---

## Artifacts

- **Log:** `docs/live4_run_2tasks_20260322_153942.txt`
- **Summary:** `artifacts/agent_eval_runs/20260322_153943_36cb3f/summary.json`
- **Outcomes:** `artifacts/agent_eval_runs/20260322_153943_36cb3f/tasks/<task_id>/outcome.json`
