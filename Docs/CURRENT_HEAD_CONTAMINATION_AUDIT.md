# Current HEAD Contamination Audit

**Date:** 2025-03-20  
**Method:** Code-first, re-derived from current HEAD (prior audits untrusted)  
**Scope:** agent/, editing/, planner/, repo_graph/, tests/agent_eval/

---

## 1. Executive Summary

### Is Core Logic Still Contaminated or Mostly Clean?

**Mostly clean, with residual contamination.** Production code (`agent/`, `editing/`, `planner/`) has no imports from `tests/`. Eval logic is isolated under `tests/agent_eval/`. The main contamination is:

1. **Benchmark-shaped module-descriptor tokens** in `target_resolution.py` (phrases2 regex)
2. **Docstring contamination** in `task_semantics.py` (mentions `benchmark_local/`)
3. **Comment contamination** in `grounded_patch_generator.py` (mentions `benchmark_local/`)
4. **Harness plan injection** — eval-only but shapes behavior when running benchmarks

No `version_meta`, `typer_ver`, `readme_bench`, or `benchmark_local/` shortcuts remain in production code paths (per Stage 30 closeout). The `_synthetic_repair` in `patch_generator.py` is now generic (multiply→div, split whitespace only).

### Top 15 Most Concerning Remnants

| # | Location | Exact Heuristic / Hard-Coded Rule | Risk |
|---|----------|-----------------------------------|------|
| 1 | `agent/retrieval/target_resolution.py` L205 | `phrases2 = re.findall(r"\b(validation\|runtime\|config\|cfg\|logging\|parser\|guard\|options\|defaults\|levels)\s+(\w+)\b", low)` — "guard", "levels" map to adversarial fixtures av09_guard, av03_levels | high |
| 2 | `agent/retrieval/target_resolution.py` L221 | `if "guard" in low and "validation" in low` — explicit guard/validation phrase for av09_guard | high |
| 3 | `agent/retrieval/target_resolution.py` L192 | `for prefix in ("cfg", "config", "impl", "core", "runtime", "validation", "logging", "io")` — fixture-shaped directory set | medium |
| 4 | `tests/agent_eval/harness.py` L118-211 | `_parent_plan_for_spec`, `_build_phase_1_steps` — plan shapes driven by `grading_mode`, `tags` (docs+consistency, explain_artifact) | medium (eval-only) |
| 5 | `tests/agent_eval/real_execution.py` L55-67 | `_compat_plan_dict_for_audit` — tags (repair, feature, refactor, tests, multi_file) → SEARCH+EDIT; else single EXPLAIN | medium (eval-only) |
| 6 | `tests/agent_eval/runner.py` L224 | `if not outcome.success and spec.grading_mode != "explain_artifact"` — semantic_rca only for non-explain tasks | low (eval-only) |
| 7 | `agent/retrieval/task_semantics.py` L28 | Docstring: "Paths like benchmark_local/check_*.py" — code is generic; docstring contaminated | low |
| 8 | `editing/grounded_patch_generator.py` L592 | Comment: "benchmark_local/" — implementation is generic; comment contaminated | low |
| 9 | `tests/agent_eval/harness.py` L290 | `_STDLIB_SHADOW_DIRS` — stdlib names for adversarial repos; eval-only | low |
| 10 | `agent/retrieval/target_resolution.py` L332 | `_STDLIB_SHADOW_CANDIDATES` — same concept in production; product-worthy | low |
| 11 | `tests/agent_eval/success.py` L19-26 | `task_success(loop_output, path_mode, exc)` — path_mode "compat" vs "hierarchical" from orchestration_path | low (eval-only) |
| 12 | `tests/agent_eval/runner.py` L118 | Suite help: "core12, audit12, audit6, holdout8, adversarial12, external6, live4, paired4" | low (eval-only) |
| 13 | `tests/agent_eval/suite_loader.py` | Hard-coded suite names in if/elif chain | low (eval-only) |
| 14 | `tests/agent_eval/paired_comparison.py` L360-452 | Canonical types: repair, feature, docs_consistency, explain_artifact, multi_file | low (eval-only) |
| 15 | `agent/orchestrator/plan_resolver.py` | `_docs_seed_plan`, `_is_docs_artifact_intent` — generic docs routing; product-worthy | N/A |

### Top 10 Heuristics That Are Actually Good Product Behavior

| # | Location | Heuristic | Why Keep |
|---|----------|-----------|-----------|
| 1 | `agent/retrieval/target_resolution.py` | `_VALIDATION_SCRIPT_PATTERNS` — scripts/assert_*, scripts/check_*, scripts/verify_*, bin/assert_*, tests/test_*.py | Generic convention: validation scripts demoted as edit targets |
| 2 | `agent/retrieval/task_semantics.py` | `instruction_suggests_docs_consistency` — agree, align, match, consistency, readme, .md, documented | Generic docs/code alignment intent |
| 3 | `agent/retrieval/target_resolution.py` | `detect_likely_import_shadowing` — ModuleNotFoundError parsing, stdlib shadow detection | Real Python bug; product-worthy |
| 4 | `agent/retrieval/task_semantics.py` | `instruction_edit_target_paths` — fix/add/edit X in path | Generic path extraction |
| 5 | `agent/retrieval/task_semantics.py` | `instruction_asks_to_modify_validation_script` | Allow validation script edits when explicitly requested |
| 6 | `editing/diff_planner.py` | `_is_valid_edit_target` — .md only when docs-consistency | Restrict edit targets by intent |
| 7 | `editing/patch_generator.py` | `_generic_multiply_to_div_return`, `_generic_split_whitespace_line_return` | Content-driven, no task_id |
| 8 | `editing/grounded_patch_generator.py` | `_try_version_constant_align`, `_try_url_constant_align` | Generic version/URL alignment from .md |
| 9 | `agent/orchestrator/plan_resolver.py` | `_is_docs_artifact_intent`, `_docs_seed_plan` | Docs lane routing; generic tokens |
| 10 | `editing/patch_executor.py` | `MAX_FILES_PER_EDIT`, `MAX_PATCH_LINES`, forbidden paths | Product safety |

---

## 2. File-by-File Contamination Inventory

### agent/retrieval/target_resolution.py

| Function | Exact Heuristic | Category | Risk | Recommendation |
|----------|-----------------|----------|------|-----------------|
| `resolve_module_descriptor_to_files` L205 | `phrases2 = re.findall(r"\b(validation\|runtime\|config\|cfg\|logging\|parser\|guard\|options\|defaults\|levels)\s+(\w+)\b", low)` | leaked contamination | high | Refactor: remove "guard", "levels"; keep generic dirs or move to config |
| `resolve_module_descriptor_to_files` L221 | `if "guard" in low and "validation" in low` → validation/guard.py | leaked contamination | high | Remove or generalize |
| `resolve_module_descriptor_to_files` L192 | `for prefix in ("cfg", "config", "impl", "core", "runtime", "validation", "logging", "io")` | ambiguous | medium | Move to config; some are generic |
| `_STDLIB_SHADOW_CANDIDATES` L332 | `{"io", "logging", "config", "parser", "ast", "types"}` | production-worthy | low | keep |
| `is_validation_script_path` | scripts/assert_*, scripts/check_*, bin/assert_*, tests/test_*.py | production-worthy | low | keep |

### agent/retrieval/task_semantics.py

| Function | Exact Heuristic | Category | Risk | Recommendation |
|----------|-----------------|----------|------|-----------------|
| `validation_check_script_paths_in_instruction` L28 | Docstring: "benchmark_local/check_*.py" | ambiguous | low | Fix docstring; code is generic |
| `instruction_suggests_docs_consistency` | agree, align, match, consistency, readme, .md, documented | production-worthy | low | keep |

### editing/grounded_patch_generator.py

| Function | Exact Heuristic | Category | Risk | Recommendation |
|----------|-----------------|----------|------|-----------------|
| `_try_version_constant_align` L592 | Comment: "benchmark_local/" | ambiguous | low | Remove comment |
| `_find_md_version_any_format` | README.md, CHANGELOG.md; ## vX.Y.Z, **X.Y.Z** | production-worthy | low | keep |

### editing/patch_generator.py

| Function | Exact Heuristic | Category | Risk | Recommendation |
|----------|-----------------|----------|------|-----------------|
| `_synthetic_repair` | Only generic_multiply_to_div_return, generic_split_whitespace_line_return | production-worthy | low | keep |

### tests/agent_eval/harness.py

| Function | Exact Heuristic | Category | Risk | Recommendation |
|----------|-----------------|----------|------|-----------------|
| `_parent_plan_for_spec` | orchestration_path "compat" vs "hierarchical" | eval-only | medium | isolate |
| `_build_phase_1_steps` | grading_mode, tags (docs+consistency, explain_artifact) → plan shape | eval-only | medium | isolate |
| `_compat_get_plan` | plan_id "bench_compat_plan", "benchmark compat task" | eval-only | low | isolate |
| `_STDLIB_SHADOW_DIRS` | logging, config, parser, ast, types | eval-only | low | keep (eval fixture handling) |

### tests/agent_eval/real_execution.py

| Function | Exact Heuristic | Category | Risk | Recommendation |
|----------|-----------------|----------|------|-----------------|
| `_compat_plan_dict_for_audit` | tags repair, feature, refactor, tests, multi_file → SEARCH+EDIT | eval-only | medium | isolate |
| `offline_llm_stubs` | grading_mode explain_artifact → explain stub with substrings | eval-only | low | isolate |

### tests/agent_eval/runner.py

| Function | Exact Heuristic | Category | Risk | Recommendation |
|----------|-----------------|----------|------|-----------------|
| `run_suite` L224 | grading_mode != "explain_artifact" → semantic_rca | eval-only | low | isolate |
| `build_arg_parser` L118 | Suite names in help text | eval-only | low | keep |

### tests/agent_eval/suite_loader.py

| Function | Exact Heuristic | Category | Risk | Recommendation |
|----------|-----------------|----------|------|-----------------|
| `load_suite`, `load_specs_for_mode` | core12, audit12, holdout8, adversarial12, external6, live4, paired4, paired8 | eval-only | low | keep |

---

## 3. Leakage Report

### Places Where tests/agent_eval Assumptions Influence Non-Test Code

**None.** Production code (`agent/`, `editing/`, `planner/`, `repo_graph/`) does not import from `tests/`. All `grading_mode`, `orchestration_path`, `task_id`, suite names are confined to `tests/agent_eval/`.

### Places Where Offline/Live Evaluation Integrity Code Changes Behavior Outside Eval-Only Scope

**None.** The integrity checks (`used_offline_stubs`, `live_model_integrity_ok`, `plan_injection_used`) are computed in `real_execution.py` and `harness.py` and written to artifacts. They do not modify production code paths. Production is invoked via `run_hierarchical`; the harness applies patches (`patch("agent.orchestrator.deterministic_runner.get_parent_plan", ...)`) only when running eval.

### Call-Chain Summary

- **Production entrypoint:** `run_hierarchical` → `get_parent_plan` → `get_plan` → planner/router. No eval imports.
- **Eval entrypoint:** `run_single_task` → `run_structural_agent` / `run_structural_agent_offline` / `run_structural_agent_live_model` → patches `get_parent_plan`, `execution_loop`, `get_plan` when in offline/compat mode.

---

## 4. Routing Contamination Report

### Routing and Planning Shortcuts That Are Benchmark-Shaped

| Location | Shortcut | Benchmark-Shaped? |
|----------|----------|-------------------|
| `plan_resolver.py` | CODE_SEARCH, CODE_EXPLAIN, INFRA → single step | No — generic router categories |
| `plan_resolver.py` | `_is_docs_artifact_intent` → `_docs_seed_plan` | No — generic docs discovery |
| `plan_resolver.py` | `_is_two_phase_docs_code_intent` → two-phase plan | No — generic mixed intent |
| `harness.py` | `_parent_plan_for_spec` by orchestration_path | Yes — compat vs hierarchical from TaskSpec |
| `harness.py` | `_build_phase_1_steps` by grading_mode, tags | Yes — docs_consistency, explain_artifact from TaskSpec |
| `real_execution.py` | `_compat_plan_dict_for_audit` by tags | Yes — repair, feature, etc. from TaskSpec |

### Hidden Plan-Shape Overrides

- **Offline mode:** `get_parent_plan` is patched to return `_parent_plan_for_spec(spec)` or `_compat_plan_dict_for_audit`-wrapped plan. Plan shape is fully determined by task spec (orchestration_path, grading_mode, tags).
- **Live mode:** No plan injection; production `get_parent_plan` / `get_plan` used.

### Explain/Docs/Edit/Artifact Conflations

- `grading_mode == "explain_artifact"` → different success criteria (artifact file + substrings), different plan shape (SEARCH+EXPLAIN+WRITE_ARTIFACT), different stub behavior in offline mode. All confined to eval.
- `instruction_suggests_docs_consistency` → used in production (diff_planner, patch_generator) to allow .md edits and docs-consistency early exit. Generic.

---

## 5. Hard-Coded String Inventory

### In Production Code (agent/, editing/)

| File | Literal | Context |
|------|---------|---------|
| `target_resolution.py` | "validation", "runtime", "config", "cfg", "logging", "parser", "guard", "options", "defaults", "levels" | phrases2 regex |
| `target_resolution.py` | "validation/guard.py", "guard.py" | guard_descriptor branch |
| `target_resolution.py` | "cfg", "config", "impl", "core", "runtime", "validation", "logging", "io" | module descriptor prefixes |
| `task_semantics.py` | "agree", "align", "match", "consistency", "readme", ".md", "documented" | instruction_suggests_docs_consistency |
| `grounded_patch_generator.py` | "README.md", "CHANGELOG.md" | _find_md_version_any_format |

### In Eval-Only Code (tests/agent_eval/)

| File | Literal | Context |
|------|---------|---------|
| `suite_loader.py` | "core12", "audit12", "holdout8", "adversarial12", "external6", "live4", "paired4", "paired8" | Suite names |
| `runner.py` | Same suite names | Help text |
| `harness.py` | "bench_compat_plan", "benchmark compat task", "stage12" | _compat_get_plan |
| `real_execution.py` | "bench_compat_", "agent_eval" | _compat_plan_dict_for_audit |
| `paired_comparison.py` | "repair", "feature", "docs_consistency", "explain_artifact", "multi_file" | Canonical task types |
| `suites/paired8.py` | "core12_mini_repair_calc", "core12_pin_requests_explain_trace", etc. | Task IDs |

---

## 6. Immediate Cleanup List Before Stage 36

### Exact Deletions/Refactors

1. **target_resolution.py L205:** Remove "guard" and "levels" from phrases2 regex; keep validation, runtime, config, cfg, logging, parser, options, defaults (generic). Or move full set to config.

2. **target_resolution.py L220-230:** Remove or generalize the `if "guard" in low and "validation" in low` branch. Replace with a more generic "validation X" phrase matcher if needed.

3. **task_semantics.py L28:** Fix docstring: remove "benchmark_local/check_*.py"; use "check_*.py, scripts/verify_*.py, bin/assert_*.py".

4. **grounded_patch_generator.py L592:** Remove "benchmark_local/" from comment.

5. **Optional:** Move `target_resolution.py` phrases2 tokens and prefix list to `config/` so they can be extended without code changes.

### Do NOT Remove (Product-Worthy)

- Validation script patterns (scripts/assert_*, scripts/check_*, etc.)
- instruction_suggests_docs_consistency
- instruction_edit_target_paths, instruction_path_hints
- _generic_multiply_to_div_return, _generic_split_whitespace_line_return
- _try_version_constant_align, _try_url_constant_align
- _STDLIB_SHADOW_CANDIDATES
- Docs seed plan, two-phase docs-code intent

---

## 7. Trace Summary

### Production Entrypoints (No Eval Imports)

- `agent/orchestrator/deterministic_runner.run_hierarchical`
- `agent/orchestrator/plan_resolver.get_plan`, `get_parent_plan`
- `planner/planner.plan`
- `agent/routing/instruction_router.route_instruction`
- `editing/diff_planner.plan_diff`
- `agent/retrieval/target_resolution.resolve_edit_targets_for_plan`

### Eval Entrypoints (Patch Production at Runtime)

- `tests/agent_eval/harness.run_single_task`
- `tests/agent_eval/real_execution.run_structural_agent_offline`
- `tests/agent_eval/real_execution.run_structural_agent_live_model`

Patches: `get_parent_plan`, `get_plan`, `execution_loop`, model client (offline stubs).
