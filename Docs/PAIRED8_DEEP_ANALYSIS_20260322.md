# Paired8 Deep Analysis — Principal Engineer Report

**Date:** 2026-03-22  
**Suite:** paired8 (8 tasks, live_model)  
**Run directory:** `artifacts/agent_eval_runs/20260322_175554_4858ed`  
**Log:** `agent-tools/1ecc179c-335b-428e-a5fd-41190040b475.txt` (5515 lines)

---

## Executive Summary

This report provides a per-task analysis of the paired8 run, tracing **why each retry/replan loop started or restarted**, identifying **component interactions** that drive failures, and classifying root causes: **prompt**, **bug**, **test case**, or **system design**.

**Key findings:**
1. **6 FATAL_FAILURE** events (execution loop stops after `MAX_SAME_ERROR_RETRIES=2` consecutive identical failures)
2. **Target-resolution vs edit-binding disconnect** — target_resolution correctly identifies source files, but `build_edit_binding` sometimes chooses test/validation files
3. **Retry loop lock-in** — critic diagnoses correctly (e.g. bad_plan), but retry instruction appends "Maintain same target: file=X" which **locks the wrong file** for subsequent attempts
4. **Insert-patch vs add-function confusion** — prompt passes symbol from edit_binding; for "add new function" tasks, passing existing symbol (e.g. is_verbose) steers model toward modifying that symbol instead of inserting at module level

---

## 1. Execution Loop Architecture (Retry/Replan Flow)

### 1.1 Loop Structure

```
plan_diff → to_structured_patches (generate_edit_proposals per change)
  → execute_patch
    → SUCCESS? → run_tests
      → tests pass? → return success
      → tests fail? → critic + retry_planner → _apply_hints → next attempt (plan_diff again)
    → FAIL? (patch_failed, target_not_found, etc.)
      → critic + retry_planner → _apply_hints → next attempt
```

### 1.2 Stop Conditions (FATAL_FAILURE)

- **same_error_count >= MAX_SAME_ERROR_RETRIES (2):** Two consecutive attempts produce the same error → stop
- **_should_retry_strategy:** Some errors (e.g. weakly_grounded_patch) may not retry
- **MAX_SEMANTIC_RETRIES (2):** For test failures, max 2 semantic retries per EDIT step

### 1.3 Retry Instruction Augmentation

On validation failure, the loop appends:
```
PREVIOUS_ATTEMPT:
PATCH: ...
FAILURE: ...
Maintain same target: file=<edit_binding.file>, symbol=<edit_binding.symbol>.
```

**Critical bug:** If `edit_binding` points to the **wrong file** (e.g. test file), the retry instruction **locks** that wrong target. The model is told to maintain the same target, so it keeps editing the wrong file.

---

## 2. Per-Task Analysis

### Task 1: core12_mini_repair_calc — ✓ SUCCESS

**Instruction:** Repair src/calc/ops.py so multiply(2, 3) == 6 and tests pass.

**Flow:**
- Plan: SEARCH_CANDIDATES → EDIT → SEARCH → EDIT
- Target: `src/calc/ops.py`, symbol `multiply` ✓
- First patch: `return a * b + 1` → `return a * b` ✓ applied
- **Inner loop (resolve_conflicts):** Plan had 4 changes; conflict_resolver split into groups. Multiple edit-proposal calls per group (one per change). First change fixed the bug; subsequent changes saw file already fixed → model produced `return a * b` → `return a * b * 1` (cosmetic no-op that passed)
- Validation passed

**Retry/replan:** None. Single pass.

**Root cause of success:** Target resolution correct; evidence and full_content consistent; simple text_sub.

---

### Task 2: core12_pin_typer_repair — ✗ edit_grounding_failure (patch_unchanged)

**Instruction:** Fix benchmark_local/bench_math.double so double(3) == 6.

**Flow:**
1. **Target resolution (correct):** `edit_targets_ranked` = [(bench_math.py, 5, imported_by_test_bench_math)]
2. **Edit binding (wrong):** `chosen_target_file` = `benchmark_local/test_bench_math.py`, symbol `test_double`
3. Model edited **test file** — added assertion message `assert double(3) == 6, 'Test for doubling 3 should return 6'` instead of fixing `double()` in bench_math.py
4. Tests failed (double(3)==5, halve(4)==4)
5. **Critic:** failure_type=bad_plan, "Review the goal and ensure the correct file and function are targeted"
6. **Retry planner:** strategy=generate_new_plan
7. **Retry instruction appended:** "Maintain same target: file=...test_bench_math.py, symbol=test_double"
8. Second attempt: model still told to edit test_bench_math.py → produced patch_unchanged (no-op or same wrong fix)
9. **FATAL_FAILURE** at step_id=2 after 2 consecutive same errors

**Root cause:** **BUG** — `build_edit_binding` selects from `ranked_context`/search results. Test file often ranks first (it imports bench_math, contains "bench_math", "double"). Target resolution correctly prefers bench_math.py via `source_file_preferred_over_validator`, but that result is not used when building edit_binding. **edit_binding uses a different code path** (ranked_context order) than target_resolution (edit_targets_ranked).

**Secondary:** Retry instruction "Maintain same target" **locks the wrong file**, preventing recovery.

---

### Task 3: core12_mini_feature_flags — ✗ edit_grounding_failure (patch_apply_failed)

**Instruction:** Add beta_enabled() -> bool in src/flags/store.py that returns False by default.

**Flow:**
1. Target: src/flags/store.py ✓ (correct file)
2. **chosen_symbol: is_verbose** — WRONG. Task asks for NEW function `beta_enabled`, not modification of `is_verbose`
3. Edit proposal prompt passes `Symbol: is_verbose` → model produces **insert** patch targeting `is_verbose` (e.g. insert at function_body_start of is_verbose)
4. Patch rejected: patch_apply_failed (insert at wrong symbol; symbol may not support insert, or AST patch failed)
5. **FATAL_FAILURE** (no retries; weakly_grounded or similar stops retry)

**Root cause:** **PROMPT + DESIGN** — For "add new function" tasks, edit_binding should pass `symbol=""` or `symbol=None` with hint "module_level_insert". Current flow picks top symbol from file (is_verbose) and passes it. Model assumes it must edit that symbol. **Prompt does not distinguish "add new function at module level" from "modify existing symbol".**

---

### Task 4: core12_pin_typer_feature — ✗ edit_grounding_failure (weakly_grounded_patch)

**Instruction:** (Add feature to bench_cli — task from paired8)

**Flow:**
1. Target: benchmark_local/bench_cli.py, symbol describe_app
2. `generation_rejected_reason: no_valid_patch_candidate` — model produced patch that failed grounding checks (weakly_grounded_patch)
3. **FATAL_FAILURE** — no retry for weakly_grounded

**Root cause:** **GENERATION** — Model output did not meet grounding invariants (evidence match, locality). Possible causes: instruction ambiguous, symbol/context mismatch, or model produced invalid JSON/structure.

---

### Task 5: core12_mini_docs_version — ✗ edit_grounding_failure (wrong_target_file)

**Instruction:** Make README.md and src/widget/constants.py agree on major.minor; scripts/check_readme_version.py must exit 0.

**Flow:**
1. **Target resolution:** edit_targets_ranked = [(README.md, 10), (constants.py, 10)] — tie, both path_hint_descriptor
2. **Chosen:** README.md (first in order)
3. Validation script: scripts/check_readme_version.py — compares README to constants.py
4. Model edited README to match constants OR edited wrong thing; patch rejected as **wrong_target_file**
5. **Wrong target:** Verifier detected patch targeted wrong file (e.g. should edit constants.py to match README, or vice versa — ambiguity in which to change)

**Root cause:** **TARGET RESOLUTION** — When instruction names two files (README + constants.py), resolution produces a tie. Picking first is arbitrary. The check script may require a specific one to change. Need heuristic: when validation script "checks" agreement, prefer the file that is the "source of truth" or the one that needs to change to satisfy the check.

---

### Task 6: core12_pin_click_docs_code — ✗ edit_grounding_failure (patch_unchanged)

**Instruction:** Update stability word in docs to match CLICK_BENCH_API_STABILITY (from bench_click_meta.py).

**Flow:**
1. **Chosen target:** benchmark_local/DECORATORS_NOTE.md
2. File content: `**experimental**` (see bench_click_meta.py)
3. Model produced: `old: "experimental\`**"`, `new: "CLICK_BENCH_API_STABILITY\`**"`
4. Patch rejected — **patch_unchanged** or **no_meaningful_diff**
5. Likely: the old snippet had a typo (backtick) or the replacement was invalid (CLICK_BENCH_API_STABILITY is a constant name, not the word to substitute)

**Root cause:** **PROMPT + UNDERSTANDING** — Model substituted constant name for the word "experimental". The instruction likely meant: use the *value* of CLICK_BENCH_API_STABILITY from bench_click_meta.py. Model may have inserted the identifier literally. Or: patch_unchanged because the substitution was rejected as no-op (e.g. verification thought it was cosmetic).

---

### Task 7: core12_pin_requests_explain_trace — ✗ unknown (first_failing_stage: SEARCH)

**Instruction:** Read TRACE_NOTE.md and src/requests/sessions.py. Write benchmark_local/artifacts/explain_out.txt describing redirect path.

**Flow:**
1. Grading mode: explain_artifact
2. Router: CODE_EXPLAIN → single EXPLAIN step
3. EXPLAIN ran, produced output
4. **Failure:** "missing artifact: benchmark_local/artifacts/explain_out.txt"
5. **first_failing_stage: SEARCH** — artifact path resolution or validation failed at SEARCH stage (artifact path not found, or explain output not written to correct path)

**Root cause:** **ARTIFACT PATH** — Explain output may have been written to a different path, or the validator expects the artifact at a specific location that wasn't created. Possible bug in WRITE_ARTIFACT or artifact_path resolution for explain_artifact tasks.

---

### Task 8: core12_pin_click_multifile — ✓ validation passed (structural_success=false)

**Instruction:** Rename shared suffix from legacy to unified in benchmark_local/part_a.py.

**Flow:**
1. File already had `SUFFIX = "unified"`
2. Instruction: "rename FROM legacy TO unified" — so we want legacy→unified
3. Model produced: `old: "unified"`, `new: "legacy"` — **backwards!** (unified→legacy)
4. **patch_unchanged** — patch rejected (wrong direction, or no-op in different sense)
5. **Validation passed** — possibly part_a was already correct, or validation doesn't strictly check part_a
6. files_modified includes part_a.py — may have been modified by prior step or fixture

**Root cause:** **PROMPT** — Model reversed the direction. "Rename from legacy to unified" means change legacy→unified. File had unified; model tried unified→legacy. Instruction phrasing ambiguous for model.

---

## 3. Component Interaction Diagram (Failure Modes)

```
                    ┌─────────────────┐
                    │   Planner       │
                    │   (steps)       │
                    └────────┬────────┘
                             │
                    ┌────────▼────────┐
                    │   SEARCH        │  ranked_context order
                    │   retrieval     │  (test files often first)
                    └────────┬────────┘
                             │
                    ┌────────▼────────────────────┐
                    │   build_edit_binding        │  ← BUG: ignores
                    │   (before EDIT)             │     target_resolution
                    └────────┬────────────────────┘     edit_targets_ranked
                             │
              ┌──────────────┼──────────────┐
              │              │              │
     edit_binding     chosen_target    edit_targets_ranked
     (file, symbol)   (may be wrong)   (correct, unused)
              │              │
              └──────┬───────┘
                     │
              ┌──────▼───────┐
              │ plan_diff    │  changes[0].file from diff_planner
              │ (diff_planner)│  uses context, can inherit wrong target
              └──────┬───────┘
                     │
              ┌──────▼───────────────┐
              │ to_structured_patches│  generate_edit_proposals
              │ (edit_proposal_gen)  │  uses edit_binding (file, symbol)
              └──────┬───────────────┘
                     │
              ┌──────▼───────┐
              │ execute_patch│  patch_apply, verify
              └──────┬───────┘
                     │
         ┌───────────┼───────────┐
         │           │           │
    SUCCESS    patch_failed   validation_failed
         │           │           │
         │     ┌─────▼─────┐ ┌───▼────┐
         │     │ critic    │ │ critic │
         │     │ retry_planner│ retry_planner
         │     └─────┬─────┘ └───┬────┘
         │           │           │
         │     "Maintain same target: file=X, symbol=Y"
         │           │           │  ← LOCK-IN: X may be wrong file
         │           └─────┬─────┘
         │                 │
         │           next attempt (same wrong target)
         │                 │
         │           same_error_count++
         │                 │
         │           FATAL_FAILURE (count >= 2)
         │
         ▼
    return success
```

---

## 4. Root Cause Summary

| Category | Count | Tasks | Primary Fix |
|----------|-------|-------|-------------|
| **BUG** | 1 | pin_typer_repair | Use target_resolution edit_targets_ranked (source preferred) when building edit_binding; don't pick first from ranked_context when source file is known |
| **PROMPT** | 2 | feature_flags, docs_code, multifile | Add "add new function" vs "modify symbol" distinction; pass module_level_insert hint; clarify "rename from A to B" direction |
| **TARGET RESOLUTION** | 1 | docs_version | When tie (README vs constants), use validation script to infer which file to edit |
| **GENERATION** | 1 | pin_typer_feature | weakly_grounded — improve evidence or relax grounding for edge cases |
| **ARTIFACT PATH** | 1 | explain_trace | Fix artifact path resolution for explain_artifact grading |

---

## 5. Retry Loop Triggers (Why Loops Restart)

| Trigger | When | Effect |
|---------|------|--------|
| **Patch apply failed** | target_not_found, no_effect_change, patch_apply_failed, wrong_target_file | critic + retry_planner → next attempt. same_error_count increments. |
| **Validation failed** | Tests fail after patch applied | critic + retry_planner + PREVIOUS_ATTEMPT + "Maintain same target" → next attempt |
| **Weakly grounded** | Generation rejected | No retry (immediate stop) |
| **Same error 2×** | Consecutive identical failure_reason_code | FATAL_FAILURE, loop stops |

**Retry loop lock-in:** The "Maintain same target" augmentation prevents the model from switching to the correct file on retry. When edit_binding is wrong, retries are futile.

---

## 6. Recommendations (Priority Order)

1. **Fix edit_binding to respect target_resolution**  
   In `build_edit_binding`, when `target_resolution.edit_targets_ranked` exists and has `source_file_preferred_over_validator`, use that file. Do not default to first from ranked_context when a preferred source is known.

2. **Remove or qualify "Maintain same target" on retry**  
   When critic says bad_plan or retrieval_miss, do NOT append "Maintain same target". Allow the next plan_diff to choose a different file. Only maintain target when failure was bad_patch (same file, different patch approach).

3. **Add module-level insert for "add new function"**  
   When instruction contains "add", "new function", "create function", pass `symbol=""` or `target_node="module_level"` and add prompt guidance for inserting at end of file or after existing functions.

4. **Docs-consistency target tie-breaking**  
   When README.md and constants.py tie, run check script in dry-run to see which file change would fix it, or prefer the one that is "canonical" (e.g. constants.py for APP_VERSION).

5. **Explain-artifact path audit**  
   Verify WRITE_ARTIFACT and explain_artifact grading use the same path. Ensure artifact_path is created before validation.

---

## Artifacts

- **Run:** `artifacts/agent_eval_runs/20260322_175554_4858ed`
- **Summary:** `summary.json`
- **Per-task:** `tasks/<task_id>/outcome.json`, `semantic_rca.json`, `loop_output_snapshot.json`
- **Log:** Full workflow log with model requests/responses
