# EDIT PIPELINE AUDIT — VALIDATION / CRITIC FAILURE ANALYSIS

## Pipeline flow (end-to-end)

```
edit_proposal (generate_edit_proposals) →
  patch_plan (to_structured_patches) →
    syntax_validation (validate_syntax_plan) →
      patch_verification (verify_patch_plan) →
        apply_patch (execute_patch) →
          validate_project →
            run_tests →
              critic / policy_engine / classification
```

---

## 1. STAGES — INPUT, OUTPUT, SUCCESS/FAILURE CONDITIONS

### Stage: edit_proposal (generate_edit_proposals)

**Location:** `agent/edit/edit_proposal_generator.py`

**INPUT:**
- `context` (ranked_context, edit_binding, instruction, project_root)
- `instruction` (user task)
- `project_root`

**OUTPUT:**
- List of proposal dicts: `[{file, symbol, patch: {action, old, new}, ...}]`
- Or empty list on failure

**SUCCESS CONDITIONS:**
- Model returns valid JSON with file, patch
- Evidence spans in `edit_binding` are subsets of file content (via `_ensure_evidence_file_consistency`)

**FAILURE CONDITIONS:**
- No `edit_binding` or empty evidence
- Model returns invalid/unparseable output
- File not found

---

### Stage: patch_plan (to_structured_patches)

**Location:** `editing/patch_generator.py`

**INPUT:**
- `plan`: `{changes: [{file, symbol, action, patch, reason}]}` from `plan_diff`
- `instruction`
- `context`

**OUTPUT:**
- `{changes: [{file, patch: {symbol, action, target_node, code}, patch_strategy}], already_correct?, patch_generation_reject?}`

**SUCCESS CONDITIONS:**
- At least one change produced (from synthetic, model proposals, or structured)
- No `patch_generation_reject == "weakly_grounded_patch"`

**FAILURE CONDITIONS:**
- `raw_changes and not changes` → `patch_generation_reject: "weakly_grounded_patch"`
- `already_correct` with no changes and tests not yet run (handled later in execution_loop)

---

### Stage: syntax_validation (validate_syntax_plan)

**Location:** `editing/syntax_validation.py`

**INPUT:**
- `patch_plan`: `{changes: [{file, patch}]}`
- `snapshot`: `dict[Path, str|None]` (file content before apply)
- `project_root`

**OUTPUT:**
- `(all_valid: bool, first_failure_result: dict | None)`
- First failure: `{valid: False, error, error_type, file}`

**SUCCESS CONDITIONS:**
- `apply_patch_in_memory(change, content)` returns non-None for each change
- For Python: `ast.parse(patched)` succeeds
- Non-Python: skipped (valid=True, skipped=True)

**FAILURE CONDITIONS:**
- `apply_patch_in_memory` returns `None` → `error_type: "patch_apply_failed"`
  - **Exact:** `old not in full_file_content` (text_sub) or AST apply raises (insert/replace/delete)
- `ast.parse(patched)` raises → `error_type: "syntax_error"`

---

### Stage: patch_verification (verify_patch_plan)

**Location:** `editing/patch_verification.py`

**INPUT:**
- `patch_plan`, `snapshot`, `context`, `project_root`
- Uses `edit_binding` from context for `targets_correct_file` check

**OUTPUT:**
- `(all_valid: bool, first_failure_result: dict | None)`
- Result: `{valid, reason, checks: {has_effect, targets_correct_file, is_local}}`

**SUCCESS CONDITIONS:**
- `has_effect` is True
- `targets_correct_file` is True
- `is_local` is True (or None for insert)

**FAILURE CONDITIONS:**
- `has_effect == False` → `reason: "no_meaningful_diff"`
  - text_sub: `old == new`
  - insert: `code` empty or `code_stripped in full_file_content`
- `targets_correct_file == False` → `reason: "targets_wrong_file"`
  - When `proposal.file` resolved path ≠ `binding.file` resolved path
- `is_local == False` → `reason: "target_not_found"`
  - text_sub: `old not in full_file_content`

---

### Stage: apply_patch (execute_patch)

**Location:** `editing/patch_executor.py`

**INPUT:**
- `patch_plan`: `{changes: [{file, patch}]}`
- `project_root`

**OUTPUT:**
- `{success, files_modified, patches_applied, patch_parse_ok, patch_apply_ok, patch_reject_reason, failure_reason_code, patch_effectiveness}`

**SUCCESS CONDITIONS:**
- All changes applied without preflight/effectiveness/validation reject
- Files written to disk

**FAILURE CONDITIONS (code-level):**
- `unique_files > MAX_FILES_PER_EDIT` (5)
- `code.count("\n") >= MAX_PATCH_LINES` (200)
- `action == "delete"` without `target_node` → `forbidden_delete`
- `_is_forbidden_path(file_path)` → `forbidden_path`
- Path outside repo → `target_not_found`
- File not found / is dir → `target_not_found` / `target_is_directory`
- `_is_non_source_edit_target` → `non_source_target`
- `_preflight_validate_patch` fails → `empty_patch` / `invalid_patch_syntax`
- text_sub: `assess_text_sub` rejects → `no_effect_change` | `unchanged_target_region` | `no_meaningful_diff`
- text_sub: `old not in src` → `target_not_found`
- text_sub: `ast.parse(new_src)` fails → `invalid_patch_syntax`
- structured: `assess_after_content_change` rejects → `unchanged_target_region` | `no_meaningful_diff`
- structured: `validate_patch` (compile) fails → `patch_apply_conflict`
- `apply_patch` raises → `_classify_patch_failure` (symbol_not_found, patch_anchor_not_found, etc.)

---

### Stage: validate_project

**Location:** `agent/runtime/syntax_validator.py`

**INPUT:**
- `project_root`
- `modified_files` (from patch_result)

**OUTPUT:**
- `{valid: bool, error: str}`

**SUCCESS CONDITIONS:**
- Python: `py_compile` on modified .py files returns 0, or `compileall -q .` returns 0
- Go/Node/Rust: build/check returns 0

**FAILURE CONDITIONS:**
- py_compile / compileall non-zero → `valid: False`, `error: <stderr>`
- Timeout → `valid: False`, `error: "syntax check timed out"`

---

### Stage: run_tests

**Location:** `agent/tools/run_tests.py` (called via `resolve_inner_loop_validation` + `run_tests`)

**INPUT:**
- `project_root`, `timeout`, `test_cmd` (from validation scope)

**OUTPUT:**
- `{passed: bool, stdout, stderr, error_type?}`

**SUCCESS CONDITIONS:**
- `passed == True` (test process exit code 0)

**FAILURE CONDITIONS:**
- `passed == False` (non-zero exit)

---

### Stage: critic / policy_engine / classification

**Location:** `agent/execution/policy_engine.py` — `classify_result`, `_execute_edit`  
**Critic:** `agent/meta/critic.py` — `diagnose` (used in execution_loop for retry hints, not for classification)

**INPUT (classify_result):**
- `action`: "EDIT"
- `result`: `{success, output, error}` from _edit_fn

**OUTPUT:**
- `ResultClassification`: SUCCESS | RETRYABLE_FAILURE | FATAL_FAILURE

**SUCCESS CONDITIONS:**
- `result.get("success") is True` → SUCCESS

**FAILURE → RETRYABLE:**
- `"exhausted" in error or "after retries" in error` AND `_is_context_related_failure(error, output)` → RETRYABLE
- `"empty" in error or "empty results" in error`
- `"patch" in error or "edit" in error or "symbol_not_found" in error`
- `"infra" in error or "returncode" in error`
- `"validation" in error or "invalid" in error`
- `"timeout" in error or "tool" in error or "fallback" in error`
- `_is_context_related_failure(error, output)` (via indicators or `failure_reason_code` in context set)

**FAILURE → FATAL:**
- `result is None` or not dict → FATAL
- `"exhausted"` / `"after retries"` AND NOT context-related → FATAL
- `attempt_history` length >= max_attempts AND NOT context-related → FATAL
- Unknown/unhandled error → FATAL (default)

**Context-related indicators:** weak grounding, missing context, symbol not found, empty, low-content, insufficient, no context, patch_anchor_not_found, weakly_grounded, no_changes_planned, empty_patch

---

## 2. FALSE FAILURE DETECTION

### Cases where patch_apply_ok==True, syntax_valid==True, tests_passed==True but result is failure

**Finding: NONE identified.**

Trace: When `patch_result.get("success")` is True, execution_loop proceeds to `validate_project`. If valid, it runs `run_tests`. If `test_result.get("passed")`, it returns:

```python
return {"success": True, "files_modified": ..., "patches_applied": ..., "attempt": attempt}
```

(execution_loop.py:596–616). There is no branch that flips this to failure when all three conditions hold. The policy engine’s `classify_result` returns SUCCESS when `result.get("success") is True` (policy_engine.py:204).

---

### Edge: noop_rejected when tests pass

**Location:** execution_loop.py:303–319, 367–375, 415–430

**Scenario:**
- `no_changes`, `already_correct`, or `no_meaningful_diff`
- Tests are run on the current (unchanged or trivially changed) codebase
- Tests pass
- `is_instruction_satisfied(current_instruction, full_content, binding)` returns False

**Outcome:** Returns failure with `noop_rejected`, `patch_apply_ok: False` (for no_meaningful_diff branch).

**Impact:** This is intentional: “tests pass but instruction not satisfied” is treated as failure. The heuristic `is_instruction_satisfied` is biased toward False (line 184: “Bias toward False (avoid false success)”). That can cause false failure when the instruction is satisfied but the heuristic disagrees.

**Root cause:** `is_instruction_satisfied` (execution_loop.py:151–184) uses simple heuristics (symbol in content, token hits). Weak or ambiguous instructions may not match.

---

## 3. VALIDATION STRICTNESS

### A. no_meaningful_diff when tests pass

**Behavior:** execution_loop.py:415–430. If `verify_result.reason == "no_meaningful_diff"`, tests are run. If tests pass and `is_instruction_satisfied`, return success. If tests pass but not satisfied, set `patch_result` to `noop_rejected` and treat as failure.

**Issue:** A patch that is considered “no meaningful diff” by verification (e.g. `old == new` or `code in full_file_content`) might still be correct. If tests pass, the heuristic rejection is conservative.

### B. already_correct when tests pass

**Behavior:** execution_loop.py:350–375. Same pattern: tests run; success only if `is_instruction_satisfied`.

**Issue:** Same risk as above.

### C. partial success (some tests pass)

**Behavior:** `run_tests` returns `passed: True` only when the test process exits 0. Partial pass is not modeled; any failure → `passed: False`.

**Issue:** No partial success handling. By design.

### D. empty patch but valid code

**Behavior:** If `patch_plan.get("already_correct")` and no changes, tests run. Success requires `is_instruction_satisfied`. Empty/unchanged code is not treated as success unless that heuristic agrees.

---

## 4. CRITIC / POLICY ENGINE ANALYSIS

### classify_result — conditions for FATAL_FAILURE

1. `result is None` or not dict  
2. `"exhausted" in error or "after retries" in error` AND NOT `_is_context_related_failure`  
3. `attempt_history` length ≥ max_attempts AND NOT context-related  
4. Unmatched error → default FATAL

### classify_result — conditions for RETRYABLE_FAILURE

1. Exhausted retries AND context-related  
2. `"empty"` / `"empty results"` in error  
3. `"patch"` / `"edit"` / `"symbol_not_found"` in error  
4. `"infra"` / `"returncode"` in error  
5. `"validation"` / `"invalid"` in error  
6. `"timeout"` / `"tool"` / `"fallback"` in error  
7. `_is_context_related_failure` true (indicators or `failure_reason_code` in context set)

### Does “after retries” always become FATAL?

No. If `_is_context_related_failure` is true, it becomes RETRYABLE. Context-related codes: `patch_anchor_not_found`, `weakly_grounded_patch`, `empty_patch`, `no_changes`.

### _execute_edit retry logic

- EDIT policy: `max_attempts: 2`, `retry_on: ["symbol_not_found"]`
- `symbol_retry(step, state)` yields up to 2 step variants
- `_is_failure("EDIT", ["symbol_not_found"], raw)` = `bool(result.get("error")) or result.get("success") is False`
- On success: returns immediately with SUCCESS
- On exhausted: sets `error: "edit failed after retries"`, passes `failure_reason_code` from last attempt for classification

### Misclassification risks

- Errors that contain “patch” or “edit” are RETRYABLE even when not context-related (e.g. `patch_rejected` for limit breach).
- `"validation" in error` maps many validation errors to RETRYABLE; some may be effectively fatal (e.g. hard schema violations).

---

## 5. PATCH VALIDATION PIPELINE

### syntax_validation

**Can incorrectly reject:**
- text_sub: `old` not in `full_file_content` when content differs slightly (whitespace, encoding). `apply_patch_in_memory` uses exact substring match.
- AST patches: exceptions during `apply_patch` may be too broad and reject valid edits.

**Error types:** `patch_apply_failed`, `syntax_error`

### patch_verification

**Can incorrectly reject:**
- `has_effect`: insert with code that already exists. `code_stripped in full_file_content` can reject valid additions (e.g. repeated constants).
- `targets_correct_file`: path normalization (`_resolve_for_comparison`) can cause mismatches (e.g. `./foo` vs `foo`).
- `is_local`: uses snapshot; if snapshot is stale, `old in full_file_content` can be wrong. Execution_loop snapshots at start of attempt, so this should be consistent.

**Reasons:** `no_meaningful_diff`, `targets_wrong_file`, `target_not_found`

### execute_patch (patch_effectiveness)

**Can incorrectly reject:**
- `assess_text_sub`: `meaningful_diff_line_count < 1` — difflib can undercount in edge cases, but typically only when before==after.
- `assess_after_content_change`: `module_append_is_meaningful` may reject new top-level bindings that look like duplicates.
- `unchanged_target_region` when `source_after == source_before` — correct.
- `no_meaningful_diff` when `meaningful_diff_line_count < 1` — rare if source actually changed.

---

## 6. TERMINATION CONDITIONS

### When execution STOPs (success)

1. `test_result.get("passed")` (execution_loop.py:595)  
2. `no_changes` + tests pass + `is_instruction_satisfied` (line 300)  
3. `already_correct` + tests pass + `is_instruction_satisfied` (line 362)  
4. `no_meaningful_diff` + tests pass + `is_instruction_satisfied` (line 421)

### When execution STOPs (failure)

1. `same_error_count >= MAX_SAME_ERROR_RETRIES` (2)  
2. `!_should_retry_strategy(err, attempt, max_attempts)`  
3. `_update_failure_state` returns True (no_progress / stagnation)  
4. `semantic_retry_count > MAX_SEMANTIC_RETRIES` (2)  
5. `attempt >= max_attempts` (fall-through at end of loop)

### When execution CONTINUEs (retry)

1. Patch apply fails → rollback, critic, retry (within same attempt or next)  
2. Syntax validation fails after apply → rollback, return failure (no retry for that attempt)  
3. Tests fail → rollback, critic, retry  
4. `_should_retry_strategy` allows retry and limits not exceeded

### Path where success could be ignored

None found. When tests pass, the loop returns success. The only gate is `is_instruction_satisfied` for no-op-like paths, and that happens before any patch apply.

---

## 7. LOG-LEVEL TRACE (simulated)

```
[edit_attempt]
  diff_plan=plan_diff(instruction, context)
  changes=diff_plan["changes"]
  patch_plan=to_structured_patches(plan, instruction, context)

  # Early exits (no patch to apply)
  if not changes: run_tests → passed? → is_instruction_satisfied? → SUCCESS else noop_rejected
  if patch_plan["already_correct"]: run_tests → passed? → is_instruction_satisfied? → SUCCESS else noop_rejected

  # Structural improvement
  structural_reject=previous_patch and (not changed or not same_target)
  if structural_reject: patch_apply_ok=False, failure_reason_code=reject_reason → FAILURE

  # Weakly grounded
  if patch_plan["patch_generation_reject"]=="weakly_grounded_patch": patch_apply_ok=False → FAILURE

  # Syntax validation
  syntax_ok, syntax_result=validate_syntax_plan(patch_plan, snapshot, project_root)
  syntax_valid=syntax_ok
  if not syntax_ok: patch_apply_ok=False, failure_reason_code=err_type → FAILURE

  # Patch verification
  verify_ok, verify_result=verify_patch_plan(patch_plan, snapshot, context, project_root)
  if not verify_ok:
    if reason=="no_meaningful_diff": run_tests → passed? → is_instruction_satisfied? → SUCCESS else noop_rejected
    else: patch_apply_ok=False → FAILURE

  # Apply
  patch_result=execute_patch(patch_plan, project_root)
  patch_apply_ok=patch_result["success"]
  if not patch_apply_ok: rollback → FAILURE (retry)

  # Post-apply
  syntax_result=validate_project(project_root, modified_files)
  if not syntax_result["valid"]: rollback → FAILURE (syntax_error)

  test_result=run_tests(project_root, timeout, test_cmd)
  tests_passed=test_result["passed"]
  if tests_passed: → SUCCESS
  else: rollback, semantic_feedback, retry or no_progress/max_attempts → FAILURE
```

**Where state becomes failure (conceptually):**

1. **structural_reject** — before syntax/verification  
2. **weakly_grounded_patch** — before syntax  
3. **syntax_validation** — `syntax_ok==False`  
4. **patch_verification** (non–no_meaningful_diff)  
5. **execute_patch** — `patch_result.success==False`  
6. **validate_project** — `syntax_result.valid==False`  
7. **run_tests** — `tests_passed==False`  
8. **noop_rejected** — `is_instruction_satisfied==False` for no-op paths  
9. **Retry limits** — same_error_count, stagnation, max_attempts  

---

## 8. OUTPUT FORMAT SUMMARY

### stages

- **edit_proposal:** input: context, instruction; output: proposals; success: valid proposals; failure: no binding / model error  
- **patch_plan:** input: plan, instruction, context; output: changes, already_correct; success: changes present; failure: weakly_grounded_patch  
- **syntax_validation:** input: patch_plan, snapshot; output: (valid, result); success: apply_in_memory ok + ast.parse ok; failure: patch_apply_failed, syntax_error  
- **patch_verification:** input: patch_plan, snapshot, context; output: (valid, result); success: has_effect, targets_correct_file, is_local; failure: no_meaningful_diff, targets_wrong_file, target_not_found  
- **apply_patch:** input: patch_plan, project_root; output: success, files_modified, etc.; success: all applied; failure: preflight, effectiveness, target_not_found, etc.  
- **validate_project:** input: project_root, modified_files; output: valid, error; success: py_compile/compileall ok; failure: syntax/timeout  
- **run_tests:** input: project_root, timeout, test_cmd; output: passed, stdout, stderr; success: passed; failure: non-zero exit  
- **classification:** input: result dict; output: SUCCESS | RETRYABLE | FATAL; success: result.success; failure: per classify_result rules  

### false_failure_cases

- **noop_rejected with tests passing:** Tests pass, but `is_instruction_satisfied` returns False. Root cause: heuristic bias. Location: `execution_loop.py:151–184`, 300–319, 367–375, 415–430.

### critic_analysis

- **Classification rules:** As in Section 4.  
- **Misclassification risks:** Broad “patch”/“edit”/“validation” → RETRYABLE; possible over-RETRYABLE for non-context errors.

### validation_issues

- **patch_verification insert `has_effect`:** `code in full_file_content` can reject valid duplicate additions. Impact: rare false rejection.  
- **is_instruction_satisfied heuristic:** Biased toward False; can reject valid no-op success. Impact: noop_rejected when tests pass.  

### final_verdict

- **Is the system biased toward failure?** **PARTIALLY YES.**  
- **Root cause:**  
  1. `is_instruction_satisfied` is conservative and can mark valid no-op completions as failure when tests pass.  
  2. Patch verification and effectiveness checks (e.g. `no_meaningful_diff`, `code in full_file_content`) can reject valid edits in edge cases.  

The main “success turned into failure” risk is the noop_rejected path when tests pass but the instruction-satisfaction heuristic disagrees. The rest of the pipeline is consistent: if patch applies, syntax is valid, and tests pass, the outcome is success.
