# Stage 22 — Semantic Wrong-Patch RCA and Execution-Quality Audit — Closeout

**Date:** 2026-03-20  
**Scope:** Root-cause analysis of adversarial12's dominant failure mode: syntactically valid EDITs that apply but fail validation.

---

## 1. Files Changed

| File | Change |
|------|--------|
| `agent/runtime/execution_loop.py` | Added RCA-grade telemetry: `_infer_patch_type`, `_extract_validation_path_from_cmd`, `_patch_touched_validation_path`; extended `_merge_patch_telemetry` with `patch_plan_summary`, `attempted_target_files`; on validation failure before rollback: `edit_rca_before_snippets`, `edit_rca_after_snippets`, `validation_command`, `validation_failure_summary`, `rollback_happened`, `patch_touched_validation_path` |
| `tests/agent_eval/semantic_rca.py` | **New** — `classify_wrong_patch_root_cause`, `build_semantic_rca_dict` |
| `tests/agent_eval/runner.py` | Write `semantic_rca.json` per failed EDIT task; add `semantic_rca_cause_histogram` to summary |

---

## 2. New Telemetry / Artifact Fields

### Per-EDIT attempt (edit_telemetry / edit_patch_telemetry)

| Field | Description |
|-------|-------------|
| `patch_plan_summary` | List of `{file, symbol, patch_strategy, patch_type}` per change |
| `attempted_target_files` | Search target candidates |
| `validation_command` | Resolved test command |
| `validation_failure_summary` | First 500 chars of stdout+stderr when validation fails |
| `rollback_happened` | True when patch applied but validation failed and we rolled back |
| `edit_rca_before_snippets` | First 400 chars per file before apply |
| `edit_rca_after_snippets` | First 400 chars per file after apply (before rollback) |
| `patch_touched_validation_path` | Heuristic: whether any modified file matches validation script path |

### Per-task semantic_rca.json (failed EDIT tasks only)

| Field | Description |
|-------|-------------|
| `task_id`, `task_type`, `instruction` | Task context |
| `explicit_path_present` | Whether instruction mentions explicit edit path |
| `retrieved_top_paths` | Top paths from retrieval |
| `chosen_edit_target`, `chosen_symbol` | What the planner chose |
| `patch_strategy`, `patch_plan_summary` | Patch plan details |
| `validation_command`, `reject_reason`, `failure_bucket` | Validation context |
| `patch_applied`, `validation_failed_after_apply` | Boolean flags |
| `target_likely_mismatched` | Heuristic from classifier |
| `guessed_root_cause` | Classifier output |
| `validation_failure_summary` | Truncated validation output |

---

## 3. Root-Cause Taxonomy

| Cause | Description |
|-------|-------------|
| `wrong_target_file` | Chose a file that is not the correct edit target |
| `wrong_symbol_or_anchor` | Correct file but wrong symbol or AST anchor |
| `patch_applied_but_behavior_unchanged` | Patch applied but did not change behavior |
| `patch_applied_but_wrong_behavior` | Patch applied but produced incorrect behavior (validation failed) |
| `validation_scope_mismatch` | Patch touched files the validation script does not exercise |
| `no_edit_attempted` | No edit was attempted (e.g. empty plan) |
| `ambiguous_instruction_or_missing_path` | Instruction lacks explicit path; retrieval/plan could not resolve target |
| `unknown` | Insufficient signal to classify |

---

## 4. adversarial12 Root-Cause Histogram (Post Stage 22)

| Cause | Count |
|-------|-------|
| `patch_applied_but_wrong_behavior` | 4 |
| `no_edit_attempted` | 2 |
| `ambiguous_instruction_or_missing_path` | 4 |

---

## 5. Case Studies (5 Failed Tasks)

### 5.1 adv_repair_ratios

- **Instruction:** Fix normalize_ratios in core/ratios.py so that 12 divided by 4 equals 3.0.
- **Target:** core/ratios.py, normalize_ratios ✓
- **Patch:** Applied (patch_apply_ok: true) but **before/after snippets identical**. The bug is `return a * b`; correct fix is `return a / b`. Test assertion: `assert normalize_ratios(12.0, 4.0) == 3.0`; actual: 48.0.
- **Root cause:** `patch_applied_but_wrong_behavior` — patch produced wrong or no-op change; patch planning/generation did not fix the bug.

### 5.2 adv_repair_parse

- **Instruction:** Fix parse_bytes in io/bytes_parser.py to split on whitespace and return a list of tokens.
- **Target:** io/bytes_parser.py ✓
- **Patch:** Applied but before/after identical. Validation failed with ImportError (test module import failed).
- **Root cause:** `patch_applied_but_wrong_behavior` — patch did not change the file; behavior unchanged.

### 5.3 adv_feature_config

- **Instruction:** Add max_retries() -> int in the runtime options module returning 3.
- **Explicit path:** No.
- **Target:** null (chosen_edit_target, chosen_symbol empty)
- **Root cause:** `ambiguous_instruction_or_missing_path` — edit_grounding_failure; retrieval/plan could not resolve "runtime options module" to runtime/options.py.

### 5.4 adv_docs_release

- **Instruction:** Align RELEASE_NOTES.md and pkg/version.py so the version in the release header matches BUILD_NUMBER.
- **Target:** pkg/version.py, RELEASE_NOTES.md, scripts/assert_release_match.py ✓
- **Patch:** Applied but before/after identical. BUILD_NUMBER still "2.0.0", RELEASE_NOTES still "## v1.5.0". Assert script expects them to match.
- **Root cause:** `patch_applied_but_wrong_behavior` — patch did not align versions; docs-consistency pattern not applied.

### 5.5 adv_multifile_const

- **Instruction:** Rename BASE_URI from 'http' to 'https' in mod_a/params.py and any dependent code.
- **Patch:** Applied (patch_apply_ok: true) but validation failed.
- **Root cause:** `patch_applied_but_wrong_behavior` — patch produced incorrect behavior or failed to update all dependent code.

---

## 6. Dominant Problem

**Patch planning/generation quality.**

The dominant cause is `patch_applied_but_wrong_behavior` (4/10 classified; plus 2 no_edit, 4 ambiguous). The RCA telemetry shows:

- **Target selection is correct** in most cases: chosen_target_file is the right file (core/ratios.py, io/bytes_parser.py, pkg/version.py).
- **patch_apply_ok: true** but **before/after snippets identical** — the patch either applied a no-op or applied to the wrong location. The planner/LLM produces patches that do not correctly fix the bug.
- No synthetic patterns match adversarial names (normalize_ratios, parse_bytes, cfg_verbose, BUILD_NUMBER, etc.); the pipeline falls through to placeholder or wrong patches.

Secondary: `ambiguous_instruction_or_missing_path` (4) — instructions without explicit paths (e.g. "runtime options module") fail to ground.

---

## 7. One Ranked Recommendation for Stage 23

**Focus on patch planning/generation quality.**

The dominant cause is `patch_applied_but_wrong_behavior`. Stage 23 should focus on:

1. **Patch planning/generation quality** — improve diff_planner and patch_generator so that when the planner produces a change, the generated patch actually implements the intended fix. This includes:
   - Better use of LLM-generated patches when no synthetic patterns match
   - Validation that the patch changes the intended region (before/after diff)
   - Fallback to text_sub when structured AST fails to produce a correct change

**Do not** prioritize target grounding or validation-scope mismatch in Stage 23; those are secondary. One focused stage.

---

## 8. Commands Run

```bash
python3 -m pytest tests/agent_eval -q
python3 -m pytest tests/test_run_hierarchical_compatibility.py tests/test_two_phase_execution.py -q
python3 -m tests.agent_eval.runner --execution-mode real --suite audit12 --output artifacts/agent_eval_runs/audit12_after_stage22_rca
python3 -m tests.agent_eval.runner --execution-mode real --suite holdout8 --output artifacts/agent_eval_runs/holdout8_after_stage22_rca
python3 -m tests.agent_eval.runner --execution-mode real --suite adversarial12 --output artifacts/agent_eval_runs/adversarial12_after_stage22_rca
```

---

## 9. Benchmark Results Summary

| Suite | Success | Total |
|-------|---------|-------|
| audit12 | 12 | 12 |
| holdout8 | 8 | 8 |
| adversarial12 | 2 | 12 |
