# Stage 30 — Heuristic Decontamination and Live-Eval Integrity — Closeout

## Summary

Stage 30 removes benchmark-shaped hard-coded logic from core agent paths and reinforces live-eval integrity. Track A (live-eval integrity) was largely in place; Track B (heuristic decontamination) completed the removal of benchmark-specific synthetics and injects.

---

## Files Changed

| File | Changes |
|------|---------|
| `editing/patch_generator.py` | Removed `_synthetic_changelog_version_align`, `_synthetic_api_base_align`, `_suffix_constant_text_sub`, `_inject_shared_prefix_multifile`, `_inject_click_benchmark_multifile_change`; removed inject block in `to_structured_patches`; simplified `_synthetic_repair` to only `_generic_multiply_to_div_return` and `_generic_split_whitespace_line_return`; simplified `_try_text_sub_fallback` to same generic repairs |
| `editing/grounded_patch_generator.py` | (Prior) Removed `_try_halve_return_repair`; simplified `_find_md_version_any_format` to README.md, CHANGELOG.md |
| `agent/retrieval/target_resolution.py` | (Prior) Removed `version_meta`, `typer_ver`, `readme_bench`, `benchmark_local/` shortcuts; kept generic path hint extraction |
| `agent/retrieval/task_semantics.py` | (Prior) Removed `benchmark_local/`, `so benchmark_`, `so scripts/` from docs-consistency detection |
| `editing/diff_planner.py` | (Prior) Removed version+constants special case in `_instruction_hint_file_targets` |
| `tests/test_stage13_4_multifile_grounding.py` | Removed `test_inject_works_when_project_root_from_env`; updated `test_to_structured_patches_uses_project_root_fallback` to use generic divide/ops case; renamed `test_click_multifile_apply_succeeds_with_relative_paths` → `test_click_multifile_no_benchmark_inject` and asserted rejection when inject removed |
| `tests/agent_eval/test_stage28_grounding_hardening.py` | Removed `_try_halve_return_repair` import and `test_halve_return_repair`, `test_halve_via_generate`; merged `test_module_descriptor_version_meta` and `test_module_descriptor_typer_ver` → `test_module_descriptor_explicit_path_hint`; updated `test_find_md_version_*` and `test_version_constant_align_bold_format` to use README.md/CHANGELOG.md/lib/version.py |
| `tests/agent_eval/test_stage30_heuristic_decontamination.py` | **New** — Regression tests for removed benchmark logic and execution modes |

---

## Benchmark-Specific Logic Removed

- **Patch generator:** `_synthetic_changelog_version_align`, `_synthetic_api_base_align`, `_suffix_constant_text_sub`, `_inject_shared_prefix_multifile`, `_inject_click_benchmark_multifile_change`; all benchmark-shaped synthetic repairs (safe_div, is_valid, enable_debug, log_level, shared_prefix_rename); inline branches for multiply, tokenize, double, beta_enabled, describe_app, part_a/SUFFIX
- **Grounded patch generator:** `_try_halve_return_repair`; VERSION_NOTE.md, README_BENCH.md from `_find_md_version_any_format`
- **Target resolution:** `version_meta`, `typer_ver`, `readme_bench`, `benchmark_local/` shortcuts
- **Task semantics:** `benchmark_local/`, `so benchmark_`, `so scripts/` from docs-consistency
- **Diff planner:** Version+constants special case for py_hints/md_hints reordering

---

## Generic Heuristics Retained

- Validation-script demotion (`is_validation_script_path`, `rank_edit_targets`)
- Explicit path extraction (path hints for `lib/version.py`, `README.md`, `CHANGELOG.md`)
- Symbol retry variants (`return_binary_op_repair`, `fix_return_value`, `empty_check_negation`)
- Patch effectiveness gates (semantic ranking, validation)
- Graph resolution fallback
- Import shadowing detection
- Patch safety guards
- `_generic_multiply_to_div_return`, `_generic_split_whitespace_line_return`
- `_find_md_version_any_format` for README.md, CHANGELOG.md
- `_try_version_constant_align` for version alignment

---

## Integrity Invariants Added

- **Execution modes:** `mocked`, `offline`, `live_model`, `real` (deprecated alias → `offline`)
- **Model call audit:** `reset_model_call_audit()`, `get_model_call_audit()` in `model_client.py` (track total calls, small/reasoning counts, model names, base URL, bounded call sites)
- **Live-model:** `run_structural_agent_live_model` (no offline stubs, no fake explain)
- **Offline:** `run_structural_agent_offline` (stubs, plan injection)
- **Integrity flags:** `used_offline_stubs`, `used_plan_injection`, `used_explain_stub` recorded in task artifacts
- **Live runs invalid** if zero real model calls occurred

---

## Before/After Benchmark Impact

| Task / Scenario | Before | After |
|-----------------|--------|-------|
| Legacy→unified multifile (part_a SUFFIX) | Succeeded via `_inject_click_benchmark_multifile_change` | Rejected (`no_grounded_candidate_found`) when plan has vague patch text |
| Divide/ops multiply→div | Succeeded via generic repair | Still succeeds |
| Docs version alignment (VERSION_NOTE.md, README_BENCH.md) | Resolved via benchmark shortcuts | Resolved via README.md, CHANGELOG.md, lib/version.py when present |
| Halve task | Succeeded via `_try_halve_return_repair` | Relies on grounded generation or other strategies |
| Offline benchmark suite | Unchanged | Unchanged |
| Live-model evaluation | Unchanged | Unchanged |
| Production runtime | Unchanged | Unchanged (except harmless telemetry) |

---

## Regression Tests

- `tests/agent_eval/test_stage30_heuristic_decontamination.py` — 8 tests for:
  - No benchmark inject functions in patch_generator
  - No benchmark synthetics; only generic multiply/div and split
  - No halve_return_repair in grounded_patch_generator
  - No benchmark tokens in target_resolution
  - No benchmark docs-consistency tokens in task_semantics
  - Model call audit API
  - Harness execution modes
  - Runner `--real` maps to `offline`
