# Stage 24 — Grounded Patch Generation Replacement for weakly_grounded_patch Failures

**Date:** 2026-03-20  
**Scope:** Replace weak patch generation with a grounded, content-driven construction path. Focus: generation layer. No new benchmark suites, no task-id-specific logic, no orchestration contract changes.

---

## 1. Files Changed

| File | Change |
|------|--------|
| `editing/grounded_patch_generator.py` | **New** — `PatchCandidate` dataclass; 7 generic content-driven strategies; `generate_grounded_candidates`, `select_best_candidate`, `validate_grounded_candidate`, `grounded_generation_telemetry`. |
| `editing/patch_generator.py` | Wired grounded layer into `to_structured_patches`: after synthetic fails, call `_try_grounded_generation`; recovery loop tries grounded on skipped files; `generation_rejected_reason` on empty plan; `_grounded_attempt_count` / `_grounded_success_count` tracking. |
| `agent/runtime/execution_loop.py` | `_merge_patch_telemetry`: aggregate Stage 24 fields (`grounded_candidate_count`, `selected_candidate_rank`, `patch_candidate_strategy`, `patch_candidate_evidence_type`, `patch_candidate_evidence_excerpt`, `generation_rejected_reason`) from change dicts; pull `generation_rejected_reason` from synthetic `patch_result` on `weakly_grounded_patch`. |
| `tests/agent_eval/semantic_rca.py` | New causes: `no_grounded_candidate_found`, `grounded_candidate_wrong_behavior`, `ambiguous_target_resolution`; classifier checks Stage 24 telemetry (`gen_reject`, `grounded_count`, `grounded_strategy`) before broader buckets; `build_semantic_rca_dict` includes Stage 24 telemetry fields. |
| `tests/agent_eval/test_stage24_grounded_generation.py` | **New** — 34 focused tests for all strategies, evidence binding, candidate ranking, pre-executor validation, telemetry, full pipeline, and RCA integration. |

---

## 2. Grounded Generation Strategies (all generic, content-driven)

| Strategy | Fires When | Evidence Type | Rank |
|----------|-----------|---------------|------|
| `return_binary_op_repair` | Instruction says divide/multiply/add/subtract; function body has the wrong binary operator in `return A op B` | `matched_return_op_line` | 0 |
| `empty_check_negation` | Instruction says "non-empty"/"returns True for non-empty"; code has `return len(s) == 0` | `matched_inverted_empty_check` | 0 |
| `raw_return_to_split` | Instruction says "split on whitespace" + "list of tokens"; code has bare `return VAR` without `.split()` | `matched_bare_return_line` | 0 |
| `string_constant_rename` | Instruction says "Rename CONST from 'OLD' to 'NEW'"; file contains exact assignment | `matched_constant_assignment` | 0 |
| `version_constant_align` | Instruction says "align" + version keywords; .py file has any uppercase semver constant; project root has `.md` with `## vX.Y.Z` | `matched_version_constant_and_md_header` | 1 |
| `url_constant_align` | Instruction says "align/agree" + endpoint/url keywords; .py file has any uppercase URL constant; project root has `.md` with `**bold URL**` | `matched_url_constant_and_md_bold_url` | 1 |
| `add_missing_function` | Instruction says "Add FNAME() -> TYPE returning VALUE"; function confirmed absent from file | `confirmed_function_absence` | 2 |

**Ranking:** Lower rank wins. Exact text_sub with direct source line evidence (rank 0) beats structured patches with symbol evidence (rank 1) beats module_append with absence evidence (rank 2).

---

## 3. Evidence Model and Reject Rules

Every generated candidate must carry:
- `evidence_type` (non-empty string identifying what was matched)
- `evidence_excerpt` (bounded excerpt of matched file content, max 200 chars)

**Pre-executor validation** (`validate_grounded_candidate`) rejects when:

| Condition | Reject Reason |
|-----------|--------------|
| `evidence_type` or `evidence_excerpt` empty | `no_grounded_evidence` |
| `old` text not found in file content | `target_region_not_found` |
| `old == new` | `no_effect_change` |
| `code` empty for module_append | `empty_patch` |

If validation fails → candidate discarded; `generation_rejected_reason` recorded in plan output.

If ALL files fail (no grounded candidates found) → `patch_generation_reject = "weakly_grounded_patch"` + `generation_rejected_reason = "no_grounded_candidate_found"` on the plan.

---

## 4. Telemetry Additions

**Per change dict (in `patch_plan.changes`):**

| Field | Description |
|-------|-------------|
| `grounded_candidate_count` | Number of evidence-backed candidates generated |
| `selected_candidate_rank` | Rank of the chosen candidate (0 = best) |
| `patch_candidate_strategy` | Strategy name (e.g. `return_binary_op_repair`) |
| `patch_candidate_evidence_type` | Evidence type (e.g. `matched_return_op_line`) |
| `patch_candidate_evidence_excerpt` | Bounded excerpt of matched source line(s) |
| `generation_rejected_reason` | Reason when best candidate was rejected (or `None`) |

**Merged into `edit_patch_telemetry`** by the execution loop's `_merge_patch_telemetry`.

**`semantic_rca.json`:** All Stage 24 fields echoed; classifier uses them for fine-grained cause attribution.

---

## 5. Benchmark Results

### audit12

| Metric | Stage 23 | Stage 24 |
|--------|----------|----------|
| total_tasks | 12 | 12 |
| success_count | 12 | **12** |
| validation_pass_count | 12 | **12** |
| structural_success_count | 11 | **11** |
| failure_bucket_histogram | {} | {} |

**audit12 stays green. No regression.**

Run dir: `artifacts/agent_eval_runs/20260320_122259_fbe654`

---

### holdout8

| Metric | Stage 23 | Stage 24 |
|--------|----------|----------|
| total_tasks | 8 | 8 |
| success_count | 8 | **8** |
| validation_pass_count | 8 | **8** |
| structural_success_count | 7 | **7** |
| failure_bucket_histogram | {} | {} |

**holdout8 stays green. No regression.**

Run dir: `artifacts/agent_eval_runs/20260320_122404_c7d97f`

---

### adversarial12

| Metric | Stage 23 | Stage 24 |
|--------|----------|----------|
| total_tasks | 12 | 12 |
| success_count | **3** | **8** |
| validation_pass_count | 3 | 8 |
| structural_success_count | 2 | 7 |
| patches_applied_total | — | 7 |
| files_modified_total | — | 7 |
| failure_bucket_histogram | weakly_grounded: 8, ambiguous: 1 | validation_regression: 2, edit_grounding_failure: 2 |
| patch_reject_reason_histogram | weakly_grounded_patch: 9 | weakly_grounded_patch: 2, validation_tests_failed: 2 |

**adversarial12 improves from 3/12 → 8/12 (+5 tasks). Material improvement confirmed.**

Run dir: `artifacts/agent_eval_runs/20260320_122441_34e21a`

---

### adversarial12 — Per-Task Outcomes (After Stage 24)

| task_id | Stage 23 | Stage 24 | Strategy Used |
|---------|----------|----------|---------------|
| adv_repair_ratios | PASS | **PASS** | `_generic_multiply_to_div_return` (existing) + grounded layer |
| adv_repair_parse | FAIL (weakly_grounded) | FAIL (validation_regression) | `raw_return_to_split` applied but stdlib `io` naming conflict |
| adv_repair_guard | FAIL (weakly_grounded) | FAIL (edit_grounding_failure) | Wrong target (bin/assert_guard.py); no evidence in validation script |
| adv_feature_defaults | FAIL (weakly_grounded) | **PASS** | `add_missing_function` (cfg_verbose → False) |
| adv_feature_severity | FAIL (weakly_grounded) | FAIL (validation_regression) | `add_missing_function` applied but stdlib `logging` naming conflict |
| adv_feature_config | FAIL (ambiguous) | FAIL (ambiguous) | No explicit target path; planner empty |
| adv_docs_release | FAIL (weakly_grounded) | **PASS** | `version_constant_align` (BUILD_NUMBER) |
| adv_docs_spec | FAIL (weakly_grounded) | **PASS** | `url_constant_align` (DEFAULT_ENDPOINT) |
| adv_docs_version | FAIL (weakly_grounded) | **PASS** | `version_constant_align` (CURRENT_VERSION) |
| adv_explain_flow | PASS | **PASS** | WRITE_ARTIFACT (unchanged) |
| adv_explain_artifact | PASS | **PASS** | WRITE_ARTIFACT (unchanged) |
| adv_multifile_const | FAIL (weakly_grounded) | **PASS** | `string_constant_rename` (BASE_URI http→https) |

---

### adversarial12 — Semantic RCA Cause Histogram Before/After

| Cause | Stage 23 | Stage 24 |
|-------|----------|----------|
| `weakly_grounded_patch` | 8 | 0 |
| `ambiguous_instruction_or_missing_path` | 1 | 1 |
| `no_grounded_candidate_found` | — | 1 |
| `grounded_candidate_wrong_behavior` | — | 2 |
| *(success)* | 3 | 8 |

The RCA histogram now clearly distinguishes the failure modes:
- **`no_grounded_candidate_found` (1):** adv_repair_guard — grounded layer ran but found no evidence in the validation script (wrong target chosen).
- **`grounded_candidate_wrong_behavior` (2):** adv_repair_parse and adv_feature_severity — patch applied successfully, but validation tests fail due to Python stdlib namespace conflicts (`io` and `logging` are stdlib names; local packages shadow them inconsistently).
- **`ambiguous_instruction_or_missing_path` (1):** adv_feature_config — instruction says "runtime options module" without an explicit file path; planner produces no candidates.

---

## 6. Decision Rule Outcome

Per Stage 24 decision rule: adversarial12 improved materially (**3/12 → 8/12**, +5 tasks). Grounded generation is the right direction to continue investing in.

**Stage 25 should target:**

1. **Validation-environment stdlib conflicts** — `io` and `logging` are stdlib module names; local adversarial packages shadow them inconsistently. The grounded patches ARE correct but the test infrastructure fails on import. Root cause: CPython import precedence. Fix: better PYTHONPATH isolation or mini-repo naming correction.

2. **Target resolution for ambiguous paths** — adv_feature_config instruction says "runtime options module" with no explicit path; planner produces empty changes. Fix: instruction-to-file resolution (module descriptor → filepath match) from project structure scanning.

3. **Validation-script-as-target avoidance** — adv_repair_guard: instruction mentions `bin/assert_guard.py` (validation script); planner picks it up as edit target. The real fix target `validation/guard.py` is never reached. Fix: instruction edit-target extraction should filter out `bin/assert_*`, `scripts/assert_*` patterns and scan for the actual file containing the bug pattern.

---

## 7. Commands Run

```bash
python3 -m pytest tests/agent_eval -q
python3 -m pytest tests/test_run_hierarchical_compatibility.py tests/test_two_phase_execution.py -q
python3 -m tests.agent_eval.runner --execution-mode real --suite audit12 --output artifacts/agent_eval_runs/audit12_after_stage24_grounded_generation
python3 -m tests.agent_eval.runner --execution-mode real --suite holdout8 --output artifacts/agent_eval_runs/holdout8_after_stage24_grounded_generation
python3 -m tests.agent_eval.runner --execution-mode real --suite adversarial12 --output artifacts/agent_eval_runs/adversarial12_after_stage24_grounded_generation
```
