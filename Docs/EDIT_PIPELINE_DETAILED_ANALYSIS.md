# Edit Pipeline — Detailed Analysis, RCA & Recommendations

**ReAct (primary):** EDIT action → `_edit_react` → `_generate_patch_once` → `execute_patch` → `run_tests`. Single attempt per edit. See [REACT_ARCHITECTURE.md](REACT_ARCHITECTURE.md).

**Legacy / full retry:** `_edit_fn` → `run_edit_test_fix_loop` (critic, retry_planner, multi-attempt).

---

## 1. High-Level Flow

```
_dispatch_react(EDIT) / _edit_fn
    │
    ├─ plan_diff(instruction, context)     → {changes: [{file, symbol, action, patch, reason}, ...]}
    ├─ resolve_conflicts(diff_plan)        → sequential groups
    │
    └─ run_edit_test_fix_loop(instruction, context, project_root)
           │
           ├─ for attempt in 1..max_attempts:
           │      ├─ plan_diff (again, per attempt)
           │      ├─ to_structured_patches(changes)  → patch_plan
           │      ├─ execute_patch(patch_plan)
           │      ├─ validate_project (syntax)
           │      └─ run_tests
           │
           └─ on failure: _critic_and_retry, _rollback_snapshot, continue
```

---

## 2. Happy Path (End-to-End)

### 2.1 Entry: `_edit_fn` (step_dispatcher)

1. **Input:** `step["description"]` = instruction (from ReAct `args["instruction"]`).
2. **Context:** `state.context` with `project_root`, `ranked_context`, `search_target_candidates`, `retrieved_symbols`, `edit_binding`, etc.
3. **Target resolution:** `plan_diff` uses `resolve_edit_targets_for_plan` + `ranked_context` + instruction hints to pick files/symbols.

### 2.2 `plan_diff` (diff_planner)

- **Sources:** `ranked_context`, `prior_phase_ranked_context`, `retrieved_symbols`, `retrieved_files`, instruction path hints.
- **Output:** `{changes: [{file, symbol, action, patch, reason}, ...]}`.
- **Behavior:** Builds `affected_symbols` from context; expands via graph (callers); produces high-level change descriptors. `patch` is often a placeholder like `"Apply changes from: {instruction}"` — actual patch comes from `edit_proposal_generator` via `to_structured_patches`.

### 2.3 `to_structured_patches` (patch_generator)

- **Input:** `{changes}` from plan_diff + instruction + context.
- **Calls:** `generate_edit_proposals` → `edit_proposal_generator` (LLM) to produce concrete patches.
- **Output:** `{changes: [{file, symbol, patch: {action, old, new} | {action, symbol, target_node, code}}, ...], already_correct?}`.
- **Patch types:** `text_sub` (old→new), `insert` (symbol + code), `already_correct`.

### 2.4 `execute_patch` (patch_executor)

- **Preflight:** Forbidden paths, path outside repo, file not found, target is dir, non-source targets.
- **text_sub:** `assess_text_sub` → check effectiveness, `old in source`, `replace(old, new, 1)`.
- **insert/AST:** `apply_patch` (ast_patcher), `assess_after_content_change`, `validate_patch`, write file.
- **Rollback:** On failure, restores from `originals` dict (in-memory; no git).

### 2.5 Syntax & Tests

- **validate_project:** Python syntax check on modified files.
- **run_tests:** Uses `resolve_inner_loop_validation` for test command (pytest, etc.).

### 2.6 Success Exit

- Returns `{success: True, files_modified, patches_applied}`.
- Index/repo_map updated for modified files.

---

## 3. Sad Paths (Failure Modes)

### 3.1 Pre-Execution (in `_edit_fn`, before loop)

| Condition | Result | failure_reason_code |
|-----------|--------|---------------------|
| No changes from plan_diff | `executed: False` | `empty_patch` |
| Path invalid / outside root | `executed: False` | `patch_anchor_not_found` |
| File not found | `executed: False` | `patch_anchor_not_found` |
| Target is directory | `executed: False` | `target_is_directory` |
| len(changes) > MAX_FILES_EDITED | `executed: False` | (error only) |
| Patch too large | `executed: False` | (error only) |

### 3.2 Inside `run_edit_test_fix_loop`

| Stage | Failure | Handling |
|-------|---------|----------|
| plan_diff returns no changes | Run tests; if pass → success (noop); if fail → inject NO_CHANGES_RETRY, continue |
| already_correct | Run tests; if pass → success; if fail → inject ALREADY_CORRECT_RETRY, continue |
| execute_patch fails | Rollback snapshot, critic+retry, inject feedback, continue |
| Syntax invalid | Rollback, **return** (no retry; `syntax_error`) |
| Tests fail | Rollback, semantic_feedback, critic+retry, inject feedback, continue |
| semantic_retry_count > MAX_SEMANTIC_RETRIES | **Return** `test_failure` |
| max_attempts exceeded | **Return** `max_attempts_exceeded` |

### 3.3 `execute_patch` Failures

| Reason | failure_reason_code |
|--------|---------------------|
| `old` not in source | `target_not_found` |
| patch effectiveness rejected (noop, unchanged) | `no_progress_repeat`, etc. |
| Python syntax error after text_sub | `invalid_patch_syntax` |
| Symbol not found (AST) | fallback to module_append or fail |
| validate_patch fails | `patch_apply_conflict` |
| Forbidden path | `non_source_target` |
| Path outside repo | `target_not_found` |

### 3.4 RCA Classifications (patch_validation_debug)

- **STATE_INCONSISTENCY:** `file_contains_old_snippet is False` — file changed between observation and patch.
- **GENERATION_CONTRACT_MISMATCH:** `old` not in evidence_span — model generated patch from wrong/excerpted context.

---

## 4. Root Cause Analysis

### 4.1 ReAct: Empty `ranked_context` on EDIT

**Problem:** In ReAct mode, SEARCH returns results only as observation. It does **not** persist them into `state.context["ranked_context"]` or `state.context["search_target_candidates"]`. When the model then chooses EDIT, `_edit_fn` sees empty `ranked_context`.

**Impact:** `plan_diff` falls back to instruction hints and `resolve_edit_targets_for_plan`. If the instruction does not name a file path, the planner has weak grounding → wrong file, wrong symbol, or `empty_patch`.

**Root cause:** `_dispatch_react` SEARCH handler returns `{success, output}` but does not call `run_retrieval_pipeline` or otherwise populate `state.context` with search results. Non-ReAct path does this at step_dispatcher L1366–1374.

### 4.2 Patch Anchor Mismatch (target_not_found)

**Problem:** `text_sub` requires `old` to be an exact substring of the file. Model may:

- Use paraphrased/cleaned text.
- Use context from a different file or version.
- Truncate or reformat (whitespace, etc.).

**Root cause:** Evidence/context passed to edit_proposal_generator can be stale, from wrong file, or not verbatim from the file.

### 4.3 Syntax Error → Hard Stop

**Problem:** Syntax failure triggers immediate return with no retry. One bad patch ends the loop.

**Root cause:** Design choice to avoid leaving repo in broken state. No “fix syntax and retry” path.

### 4.4 Semantic Retry Limit (MAX_SEMANTIC_RETRIES)

**Problem:** After 2 test failures with semantic feedback, loop returns `test_failure` even if `max_attempts` allows more.

**Root cause:** Separate cap for “same semantic failure” to avoid spinning on identical mistakes.

### 4.5 Critic / Retry Hints Complexity

**Problem:** `_critic_and_retry` runs critic + retry_planner, producing hints that rewrite `context["instruction"]`. This can conflict with ReAct’s model-driven flow (model already gets raw failure in observation).

**Root cause:** Edit loop was built for deterministic mode where a separate planner/critic drives retries.

---

## 5. Recommendations

### 5.1 Fix ReAct Context Propagation (HIGH) ✅ DONE

**Action:** When ReAct SEARCH succeeds, persist results into `state.context`:

- `state.context["ranked_context"]` = normalized results (file, symbol, snippet/content).
- `state.context["search_target_candidates"]` = list of file paths.

**Location:** `_dispatch_react` SEARCH branch, after `_search_react` returns. Implemented in `_persist_react_search_to_context`.

**Rationale:** EDIT needs grounded context. Without it, plan_diff produces weak or wrong targets.

### 5.2 Optional: Syntax Retry

**Action:** On syntax failure, inject feedback and allow one retry instead of immediate return.

**Trade-off:** More attempts vs. risk of repeated syntax errors. Could be gated by a flag.

### 5.3 Make Critic Passive in ReAct

**Action:** When `react_mode`, skip or simplify `_critic_and_retry`. Use only:

- Raw failure text (already injected).
- `format_stateful_feedback_for_retry` (attempted actions, etc.).

Avoid instruction rewriting by retry_planner; let the ReAct model interpret the observation.

### 5.4 Improve Patch Evidence Invariant

**Action:** Ensure `edit_binding` and evidence passed to `edit_proposal_generator` are always verbatim from the current file. Recompute from file content when evidence is not a substring of full content (as in `_ensure_evidence_file_consistency`).

### 5.5 Clear Failure Reason Mapping

**Action:** Document `failure_reason_code` → user-facing message and suggested next step. Helps the model understand “target_not_found” vs “syntax_error” vs “test_failure”.

### 5.6 Telemetry for ReAct EDIT Failures

**Action:** Log `ranked_context_items`, `search_target_candidates` count, and `plan_diff changes` count when EDIT fails with `empty_patch` or `patch_anchor_not_found`. Enables diagnosing context propagation issues.

---

## 6. Data Flow Summary

```
ReAct SEARCH
  → _search_react → returns {results} to model (observation)
  → state.context["ranked_context"] NOT updated  ← BUG

ReAct EDIT
  → _edit_fn receives state.context
  → plan_diff(instruction, context)
       ranked_context = []  ← empty in ReAct
       → falls back to instruction hints, resolve_edit_targets_for_plan
  → to_structured_patches → generate_edit_proposals (LLM)
  → execute_patch
  → validate_project, run_tests
  → on failure: rollback, critic, retry
```

---

## 7. ReAct Simplified Edit Pipeline (Implemented)

ReAct EDIT uses a flattened flow—no nested loops, critic, or retry_planner:

```
_edit_react:
  1. instruction = step["description"]
  2. build_edit_binding(state)  # from ranked_context (populated by SEARCH)
  3. patch_plan = _generate_patch_once(instruction, context)
     → generate_edit_proposals (LLM), no plan_diff
  4. snapshot files
  5. result = execute_patch(patch_plan)
  6. if fail: rollback, return observation
  7. validate_project (syntax) → if fail: rollback, return observation
  8. run_tests → if fail: rollback, return observation
  9. return success
```

- **Removed for ReAct:** run_edit_test_fix_loop, multiple attempts, critic, retry_planner, semantic_feedback loop.
- **Model-driven recovery:** failures become observations; the ReAct model decides next action.

---

## 8. File Reference

| Component | File | Key Functions |
|-----------|------|---------------|
| Edit entry | `agent/execution/step_dispatcher.py` | `_edit_fn` (deterministic), `_edit_react` (ReAct), `_dispatch_react` |
| Edit loop | `agent/runtime/execution_loop.py` | `run_edit_test_fix_loop`, `_run_loop` |
| Plan | `editing/diff_planner.py` | `plan_diff` |
| Patch gen | `editing/patch_generator.py` | `to_structured_patches` |
| Proposal | `agent/edit/edit_proposal_generator.py` | `generate_edit_proposals` |
| Apply | `editing/patch_executor.py` | `execute_patch` |
| Critic | `agent/meta/critic.py` | `diagnose` |
| Retry | `agent/meta/retry_planner.py` | `plan_retry` |
