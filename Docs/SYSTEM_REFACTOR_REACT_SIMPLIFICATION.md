# SYSTEM REFACTOR — ReAct Simplification (Audit-Driven, Safe Execution)

## Step 1 — Consolidated Component List

| Component | Category | Source | Reason |
|-----------|----------|--------|--------|
| **check_structural_improvement** | REMOVE | Audit + RCA | Blocks execution BEFORE apply_patch; ReAct relies on execution feedback |
| **verify_patch_plan** | REMOVE | Audit + EDIT_PIPELINE | Blocks before apply (has_effect, targets_correct_file, is_local) |
| **validate_syntax_plan** (pre-apply) | SIMPLIFY | Audit + MINIMAL_RCA | Blocks before apply; optional—post-apply validate_project catches |
| **failure_state stagnation termination** | REMOVE | Audit | _update_failure_state returns True → early exit; artificial |
| **attempted_patches** (in check_structural) | REMOVE | Audit | Part of check_structural_improvement; blocks pre-apply |
| **is_instruction_satisfied** | REMOVE | Audit + MINIMAL_RCA | Heuristic gates success; ReAct: run tests, if pass = success |
| **no_meaningful_diff special branch** | REMOVE | Audit | Complex; use run tests → pass = success |
| **already_correct / no_changes paths** | SIMPLIFY | Audit | Remove instruction_satisfied gate; tests pass = success |
| **_should_retry_strategy** | REMOVE | Audit | Gates retries; ReAct: retry until max_attempts |
| **_update_same_error / MAX_SAME_ERROR_RETRIES** | REMOVE | Audit | Same error repeated → early termination; blocks retries |
| **strategy_explorer** | REMOVE | Audit | Over-engineering; max_attempts suffices |
| **policy_engine mutations** (query_variants, symbol_retry) | SIMPLIFY | Audit + RCA | Mutates model outputs; keep retry loop only |
| **context_pruner heuristics** | SIMPLIFY | RCA (context_pruner) | Aggressive skip when remaining<80; keep size limiting |
| **retry_planner** | OPTIONAL | Audit | Deep mode; build_retry_context |
| **critic** | OPTIONAL | Audit | Deep mode; analyze failure |
| **validate_step** (orchestrator) | REMOVE | Audit | Redundant; result already observed |

---

## Step 2 — Guarded Removal Set

### PHASE 1 — HARD REMOVE (blocks before apply_patch; not tracing/citation)

- check_structural_improvement
- verify_patch_plan  
- validate_syntax_plan (pre-apply)
- is_instruction_satisfied
- failure_state stagnation termination
- _update_same_error / MAX_SAME_ERROR_RETRIES
- _should_retry_strategy
- strategy_explorer (_run_strategy_explorer)
- no_meaningful_diff / already_correct instruction_satisfied gates

### PHASE 2 — SIMPLIFY

- policy_engine: remove query_variants, symbol_retry; keep retry loop
- context_pruner: remove aggressive skip; keep simple size limit
- validation: move to after execution (validate_project stays)

### PHASE 3 — OPTIONAL (deep mode)

- critic
- retry_planner
- trajectory memory
- strategy explorer (removed in Phase 1; no re-add)

---

## Step 5 — Final Summary (Verification Complete)

### 1. Components Removed (PHASE 1)

| Component | Status |
|-----------|--------|
| check_structural_improvement | REMOVED |
| verify_patch_plan | REMOVED |
| validate_syntax_plan (pre-apply) | REMOVED |
| is_instruction_satisfied | REMOVED |
| failure_state stagnation termination | REMOVED |
| _update_same_error / MAX_SAME_ERROR_RETRIES | REMOVED |
| _should_retry_strategy | REMOVED |
| strategy_explorer (_run_strategy_explorer) | REMOVED |
| no_meaningful_diff / already_correct instruction_satisfied gates | REMOVED (now: tests pass = success) |

### 2. Components Simplified (PHASE 2)

| Component | Change |
|-----------|--------|
| context_pruner | Removed MIN_FALLBACK_CHARS, aggressive skip-when-remaining<80; now truncates to fit |
| policy_engine | NOT modified (tests expect query_variants, symbol_retry; update tests before removal) |

### 3. Components Moved to Optional (PHASE 3)

- critic (still used for feedback; can move to deep mode later)
- retry_planner
- trajectory memory

### 4. Components Kept (CORE)

- plan_diff, to_structured_patches, execute_patch, run_tests
- retrieval pipeline, repograph, search, grep
- trace_logger (log_event, step_executed via step_dispatcher/orchestrator)
- ranked_context, retrieved_files (citation)
- validate_project (post-apply)
- extract_semantic_feedback, _update_failure_state (feedback only)
- MAX_PATCH_FILES, MAX_PATCH_LINES (safety limits)

### 5. BEFORE vs AFTER Execution Flow

**BEFORE:**
```
plan_diff → to_structured_patches
  → [check_structural_improvement → reject?] 
  → [validate_syntax_plan → reject?]
  → [verify_patch_plan → reject?]
  → [is_instruction_satisfied → success?]
  → execute_patch → validate_project → run_tests
  → [stagnation/same_error/retry_guard → terminate?]
  → extract_feedback, critic, retry
```

**AFTER:**
```
plan_diff → to_structured_patches
  → [no_changes? → run_tests → pass=success]
  → [already_correct? → run_tests → pass=success]
  → execute_patch (always) → validate_project → run_tests
  → [failure?] extract_semantic_feedback, _update_failure_state (feedback only), critic, inject, retry
  → Retry until max_attempts (no stagnation/same_error/retry_guard termination)
```

**Note:** weakly_grounded_patch no longer blocks — we always call execute_patch. When changes=[], execute_patch returns success (no-op) → run_tests → feedback from test failure.

### 6. Confirmation Checklist

| Check | Status |
|-------|--------|
| No pre-execution blockers (check_structural, verify_patch_plan, validate_syntax) | ✓ |
| Execution-driven loop active (retry on test/exec failure only) | ✓ |
| Tracing intact (log_event, step_executed via dispatcher) | ✓ |
| Citation intact (ranked_context, retrieved_files) | ✓ |

### 7. Risks / Follow-ups

1. **policy_engine mutations:** REACT_MODE=1 disables query_variants and symbol_retry. Set `REACT_MODE=1` to enable. Tests patch REACT_MODE=False when verifying mutation behavior.

---

## Post-Simplification Fixes (Critical Issues)

### ISSUE 1 — weakly_grounded_patch was still a blocker (FIXED)

**Problem:** Pre-execution decision to skip execute_patch when patch_generation_reject=weakly_grounded_patch.

**Fix:** Always call execute_patch. No synthetic failure. Let execute_patch run; if changes=[], it returns success (no-op) → run_tests → feedback from tests. Learning from execution, not pre-rejection.

### ISSUE 2 — Policy engine mutations (FIXED)

**Problem:** query_variants and symbol_retry mutate model outputs → breaks Thought → Action → Observation.

**Fix:** Added `REACT_MODE` (config/agent_runtime.py). When `REACT_MODE=1`:
- SEARCH: disable get_initial_search_variants; use retrieval_input as-is
- EDIT: use retry_same(step) instead of symbol_retry(step, state)

Enable with `REACT_MODE=1` (env).
2. **weakly_grounded_patch:** FIXED. No longer blocks — always call execute_patch; let it fail and learn from error.
3. **Safety limits:** MAX_PATCH_FILES/MAX_PATCH_LINES still reject oversized patches before apply. Kept intentionally.
