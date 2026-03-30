# Repo-Wide Heuristic and Hardcoding Audit

**Date:** 2025-03-20  
**Scope:** AutoStudio codebase (code-first; excludes docs/closeouts unless cross-checking)  
**Purpose:** Identify drift from a general software AI assistant; create cleanup map.

---

## 1. Executive Summary

### Overall Assessment

**The system is mixed — leaning benchmark-shaped.** Core infrastructure (retrieval pipeline, policy engine, execution loop) is reasonably generic, but the editing and patch-generation layers contain substantial benchmark-shaped logic. The patch generator fallback layer and target resolution layer are the most overfit. Many heuristics were introduced to pass specific benchmark tasks (holdout8, adversarial12, docs-consistency, explain-artifact) and have not been generalized.

### Top 10 Highest-Risk Heuristic Clusters

1. **Patch generator synthetic repairs** — `_synthetic_docs_stability_align`, `_synthetic_docs_httpbin_align`, `_inject_click_benchmark_multifile_change` hard-code `benchmark_local/`, `DECORATORS_NOTE.md`, `bench_click_meta.py`, `HTTPBIN_NOTE.md`, `bench_requests_meta.py`, `part_a.py`.

2. **Target resolution module descriptor tokens** — `version_meta`, `typer_ver`, `readme_bench`, `benchmark_local/` in `resolve_module_descriptor_to_files`; `VERSION_NOTE.md`, `README_BENCH.md` in `_find_md_version_any_format`.

3. **Validation script path patterns** — `scripts/assert_`, `scripts/check_`, `scripts/verify_`, `bin/assert_`, `bin/check_`, `bin/verify_` baked into target resolution and task semantics; benchmark-specific naming.

4. **Patch generator function-name–specific repairs** — `safe_div`, `is_valid`, `enable_debug`, `log_level`, `beta_enabled`, `describe_app`, `multiply`, `tokenize`, `double`, `halve`; `SHARED_PREFIX`; `SUFFIX = 'legacy'` → `'unified'`.

5. **Stdlib shadowing directory list** — `_STDLIB_SHADOW_CANDIDATES` in `target_resolution.py` and `_STDLIB_SHADOW_DIRS` in `harness.py`; `_transform_pytest_cmd_for_shadowing` rewrites pytest commands for adversarial repos.

6. **Docs-consistency alignment rules** — `APP_VERSION`, `constants.py`, `Current release:`, `CLICK_BENCH_API_STABILITY`, `DECORATORS_NOTE.md`, `DEFAULT_HTTPBIN_BASE`, `HTTPBIN_NOTE.md`, `API.md`, `spec/api_spec.py`; path hints `"benchmark_local/"`, `"benchmark_"` in instruction.

7. **Harness grading_mode / orchestration_path** — `grading_mode` (`structural_loop`, `validation_exit_code`, `explain_artifact`), `orchestration_path` (`compat`, `hierarchical`), `tags` (`docs`, `consistency`) drive routing in harness and real_execution.

8. **Retry guard failure types** — `syntax_error`, `timeout` only allow 1 retry; `unknown` blocks retries; `retrieval_miss`, `bad_patch`, `test_failure` allow retries; hard-coded enum.

9. **Failure bucket classification** — `failure_class`, `edit_failure_reason`, `validation_tests_failed`, `phase_validation_failed`; string matching on notes and error text.

10. **Semantic RCA cause inference** — `classify_wrong_patch_root_cause` maps telemetry fields to 20+ cause labels; many branches are tied to benchmark-specific telemetry.

### Top 10 Most Product-Appropriate Generic Heuristics

1. **Validation script demotion** — `is_validation_script_path` patterns `test_*.py`, `*_test.py`, `scripts/assert_*.py`, `scripts/check_*.py`, `scripts/verify_*.py`; generic convention that tests/assert scripts are not primary edit targets.

2. **Patch effectiveness gates** — `assess_text_sub`, `module_append_is_meaningful`; reject no-op and unchanged edits.

3. **Forbidden path patterns** — `FORBIDDEN_PATH_PATTERNS` (`.env`, `secrets/`, `.key`, `.pem`); `_is_blocked_edit_path` for index artifacts.

4. **Symbol retry mutation** — `file_level`, `symbol_short`, `alternate_target`; generic retry strategies when symbol-level fails.

5. **Instruction path hints** — Regex extraction of `path/to/file.py` from instruction; generic.

6. **Policy engine retry limits** — `max_attempts` per action (SEARCH: 5, EDIT: 2, INFRA: 2); configurable.

7. **Graph symbol resolution fallback** — `_resolve_symbol_to_id` tries exact, short name, module.symbol; generic.

8. **Import shadowing detection** — `detect_likely_import_shadowing` parses `ModuleNotFoundError` / `ImportError`; generic stdlib shadow detection.

9. **`_looks_like_code` heuristic** — Reject planner prose ("Apply changes from:") as patch code; generic.

10. **Patch execution safeguards** — `MAX_FILES_PER_EDIT`, `MAX_PATCH_LINES`, `forbidden_delete`; product safety.

---

## 2. Audit Method

### Directories Inspected

- `agent/` — retrieval, execution, runtime, memory, meta, orchestrator
- `editing/` — diff_planner, patch_generator, grounded_patch_generator, patch_executor, patch_effectiveness
- `repo_graph/` — graph_builder, graph_storage, graph_query
- `agent/tools/` — validation_scope, run_tests
- `tests/agent_eval/` — harness, runner, real_execution, failure_buckets, semantic_rca, workspace_artifacts
- `config/` — editing_config, agent_runtime
- `planner/` — planner_utils

### Search Strategy

- **Grep:** `task_id`, `grading_mode`, `benchmark_local`, `check_`, `verify_`, `assert_`, `version_meta`, `typer_ver`, `readme_bench`, `safe_div`, `is_valid`, `enable_debug`, `log_level`, `beta_enabled`, `describe_app`, `multiply`, `tokenize`, `double`, `halve`, `SHARED_PREFIX`, `SUFFIX`, `part_a`, `bench_click`, `HTTPBIN`, `DECORATORS_NOTE`, `API.md`, `spec/api_spec.py`; `audit12`, `holdout8`, `adversarial12`, `external6`.
- **Read:** Full implementation of `target_resolution.py`, `task_semantics.py`, `grounded_patch_generator.py`, `patch_generator.py`, `diff_planner.py`, `patch_executor.py`, `patch_effectiveness.py`, `mutation_strategies.py`, `policy_engine.py`, `execution_loop.py`, `harness.py`, `real_execution.py`, `semantic_rca.py`, `failure_buckets.py`, `validation_scope.py`, `retry_guard.py`, `workspace_artifacts.py`, `graph_builder.py`.
- **Call-chain tracing:** `plan_diff` → `resolve_edit_targets_for_plan` → `rank_edit_targets`; `to_structured_patches` → `_synthetic_repair` → `_generic_*` / `_synthetic_*`; `run_single_task` → `compute_success` → `classify_failure_bucket`.

### Definition of “Heuristic” or “Hard-Coded Rule”

- **Heuristic:** Regex, keyword list, or string-matching that drives a branch or ranking without a learned model or external config.
- **Hard-coded rule:** Literal path, filename, constant, or function name in a branch.
- **Benchmark-shaped:** Logic that would only fire for a specific benchmark task, suite, or fixture layout.

---

## 3. Heuristic Inventory by Subsystem

### 3.1 Planner

| File | Function / Class | Behavior | Why | Category | Risk | Impact | Recommendation |
|------|------------------|----------|-----|----------|------|--------|----------------|
| `planner/planner_utils.py` | `ALLOWED_ACTIONS` | Fixed set of actions (SEARCH, EDIT, EXPLAIN, INFRA) | Policy enforcement | product policy | low | None | keep |

### 3.2 Retrieval

| File | Function | Behavior | Why | Category | Risk | Impact | Recommendation |
|------|----------|----------|-----|----------|------|--------|----------------|
| `agent/retrieval/target_resolution.py` | `_VALIDATION_SCRIPT_PATTERNS` | Regex for `scripts/assert_`, `scripts/check_`, `scripts/verify_`, `bin/assert_`, `bin/check_`, `bin/verify_`, `tests/test_*.py`, `*_test.py` | Demote validation scripts as edit targets | generic heuristic | low | Low | keep |
| `agent/retrieval/target_resolution.py` | `resolve_module_descriptor_to_files` | `version_meta`, `typer_ver`, `readme_bench`, `benchmark_local/` tokens; `VERSION_NOTE.md`, `README_BENCH.md`; `cfg`, `config`, `impl`, `core`, `runtime`, `validation`, `logging`, `io` prefixes | Infer source files from instruction | benchmark-shaped heuristic | high | High | refactor |
| `agent/retrieval/target_resolution.py` | `_STDLIB_SHADOW_CANDIDATES` | `io`, `logging`, `config`, `parser`, `ast`, `types` | Detect import shadowing | product policy | medium | low | move to config |
| `agent/retrieval/task_semantics.py` | `instruction_suggests_docs_consistency` | Keywords: `agree`, `align`, `match`, `consistency`, `readme`, `.md`, `so scripts/`, `so benchmark_`, `benchmark_local/`, `documented` | Docs-consistency routing | benchmark-shaped heuristic | high | High | refactor |
| `agent/retrieval/task_semantics.py` | `validation_check_script_paths_in_instruction` | Regex for `check_*.py`, `scripts/*.py`, `bin/assert_`, `bin/check_`, `bin/verify_` | Validation script paths | validation-command dependence | medium | medium | keep |

### 3.3 Task Semantics

| File | Function | Behavior | Why | Category | Risk | Impact | Recommendation |
|------|----------|----------|-----|----------|------|--------|----------------|
| `agent/retrieval/task_semantics.py` | `instruction_asks_to_modify_validation_script` | Keywords: `modify the test`, `update the test`, `edit bin/assert`, etc. | Allow validation script edits when explicitly requested | generic heuristic | low | low | keep |
| `agent/retrieval/task_semantics.py` | `instruction_edit_target_paths` | Regex for `(fix|add|edit|modify|change) ... in path` | Extract explicit edit targets | generic heuristic | low | low | keep |
| `agent/retrieval/task_semantics.py` | `instruction_path_hints` | Regex for `.py`, `.md`; auto-add `README.md` when `readme` in instruction | Path hints | generic heuristic | low | low | keep |

### 3.4 Target Resolution

| File | Function | Behavior | Why | Category | Risk | Impact | Recommendation |
|------|----------|----------|-----|----------|------|--------|----------------|
| `agent/retrieval/target_resolution.py` | `rank_edit_targets` | Penalties: 0 (explicit), 5 (inferred), 10 (descriptor), 20 (validation requested), 100 (validation demoted) | Prefer source over validation | benchmark-shaped heuristic | medium | medium | refactor |
| `agent/retrieval/target_resolution.py` | `MAX_RANKED_TARGETS = 15` | Cap ranked targets | Bounded output | product policy | low | low | move to config |
| `agent/retrieval/target_resolution.py` | `resolve_module_descriptor_to_files` | `version_meta.py`, `benchmark_local/version_meta.py`, `lib/version.py`; `typer_ver.py`, `benchmark_local/typer_ver.py` | Path hints | benchmark-shaped heuristic | high | High | remove |

### 3.5 Diff Planner

| File | Function | Behavior | Why | Category | Risk | Impact | Recommendation |
|------|----------|----------|-----|----------|------|--------|----------------|
| `editing/diff_planner.py` | `_instruction_hint_file_targets` | docs-consistency: prefer edit target by `version` + `constants` / `app_version`; py_hints + md_hints ordering | Anchor edit plan | benchmark-shaped heuristic | high | High | refactor |
| `editing/diff_planner.py` | `_is_valid_edit_target` | `.md` only when `instruction_suggests_docs_consistency` | Restrict edit targets | docs-consistency alignment | medium | medium | keep |

### 3.6 Grounded Patch Generation

| File | Function | Behavior | Why | Category | Risk | Impact | Recommendation |
|------|----------|----------|-----|----------|------|--------|----------------|
| `editing/grounded_patch_generator.py` | `_try_halve_return_repair` | Hard-codes `def halve` and `return n // 2` | Fix halve function | benchmark-shaped heuristic | high | High | remove |
| `editing/grounded_patch_generator.py` | `_find_md_version_any_format` | `benchmark_local/` in search_dirs; `VERSION_NOTE.md`, `README_BENCH.md` in cand_names | Version finder | benchmark-shaped heuristic | high | High | refactor |
| `editing/grounded_patch_generator.py` | `_apply_semantic_ranking` | `severity`, `level`, `warn`, `info`, `debug`, `error`; `-> str`, `-> bool`, `-> int` | Semantic ranking | looks generic but benchmark-shaped | medium | medium | keep |

### 3.7 Patch Generator Fallback Layer

| File | Function | Behavior | Why | Category | Risk | Impact | Recommendation |
|------|----------|----------|-----|----------|------|--------|----------------|
| `editing/patch_generator.py` | `_synthetic_docs_version_align` | `APP_VERSION`, `constants.py`, `Current release:`, `version:` | Docs version align | benchmark-shaped heuristic | high | High | remove |
| `editing/patch_generator.py` | `_synthetic_docs_stability_align` | `benchmark_local/DECORATORS_NOTE.md`, `benchmark_local/bench_click_meta.py`, `CLICK_BENCH_API_STABILITY` | Docs stability | benchmark-shaped heuristic | high | High | remove |
| `editing/patch_generator.py` | `_synthetic_docs_httpbin_align` | `benchmark_local/HTTPBIN_NOTE.md`, `benchmark_local/bench_requests_meta.py`, `DEFAULT_HTTPBIN_BASE` | Docs httpbin | benchmark-shaped heuristic | high | High | remove |
| `editing/patch_generator.py` | `_synthetic_safe_div_repair` | `safe_div`, `divide`, `return a * b` → `return a / b` | Holdout repair | benchmark-shaped heuristic | high | High | remove |
| `editing/patch_generator.py` | `_synthetic_is_valid_repair` | `is_valid`, `return len(s) == 0` → `return len(s) > 0` | Holdout repair | benchmark-shaped heuristic | high | High | remove |
| `editing/patch_generator.py` | `_synthetic_enable_debug` | `enable_debug` → `return False` | Holdout repair | benchmark-shaped heuristic | high | High | remove |
| `editing/patch_generator.py` | `_synthetic_log_level` | `log_level` → `return "INFO"` | Holdout repair | benchmark-shaped heuristic | high | High | remove |
| `editing/patch_generator.py` | `_synthetic_shared_prefix_rename` | `SHARED_PREFIX` `old` → `new` | Holdout repair | benchmark-shaped heuristic | high | High | remove |
| `editing/patch_generator.py` | `_synthetic_changelog_version_align` | `CHANGELOG.md`, `lib/version.py`, `RELEASE_VERSION` | Changelog align | benchmark-shaped heuristic | medium | medium | refactor |
| `editing/patch_generator.py` | `_synthetic_api_base_align` | `API.md`, `spec/api_spec.py`, `API_BASE` | API align | benchmark-shaped heuristic | medium | medium | refactor |
| `editing/patch_generator.py` | `_generic_multiply_to_div_return` | `return a * b` → `return a / b` when instruction has `divide` | Generic repair | generic heuristic | low | low | keep |
| `editing/patch_generator.py` | `_generic_split_whitespace_line_return` | `return line` → `return line.split()` when instruction has `split` + `whitespace` | Generic repair | generic heuristic | low | low | keep |
| `editing/patch_generator.py` | Inline `multiply`, `tokenize`, `double`, `beta_enabled`, `describe_app`, `part_a`/`SUFFIX` | Function-name–specific patches | Benchmark repair | benchmark-shaped heuristic | high | High | remove |
| `editing/patch_generator.py` | `_inject_click_benchmark_multifile_change` | `benchmark_local/part_a.py`, `part_a`, `unified`, `legacy` | Multifile inject | benchmark-shaped heuristic | high | High | remove |
| `editing/patch_generator.py` | `_inject_shared_prefix_multifile` | `pkg_a/constants.py` | Multifile inject | benchmark-shaped heuristic | high | High | remove |

### 3.8 Patch Executor / Effectiveness Gates

| File | Function | Behavior | Why | Category | Risk | Impact | Recommendation |
|------|----------|----------|-----|----------|------|--------|----------------|
| `editing/patch_executor.py` | `MAX_FILES_PER_EDIT = 5`, `MAX_PATCH_LINES = 200` | Safeguards | Product safety | product policy | low | low | keep |
| `editing/patch_executor.py` | `_classify_patch_failure` | String matching on `symbol not found`, `target is directory`, `empty_patch`, etc. | Failure classification | generic heuristic | low | low | keep |
| `editing/patch_effectiveness.py` | `module_append_is_meaningful` | Reject if no new def/class or binding | No-op gate | generic heuristic | low | low | keep |
| `editing/patch_effectiveness.py` | `assess_text_sub` | Reject `empty_patch`, `no_effect_change`, `unchanged_target_region` | No-op gate | generic heuristic | low | low | keep |

### 3.9 Validation Scope / Test Runner

| File | Function | Behavior | Why | Category | Risk | Impact | Recommendation |
|------|----------|----------|-----|----------|------|--------|----------------|
| `agent/tools/validation_scope.py` | `_scope_kind_for_command` | `benchmark_local/` in cmd → `benchmark_local` | Scope kind | validation-command dependence | medium | medium | keep |
| `tests/agent_eval/harness.py` | `_transform_pytest_cmd_for_shadowing` | Rewrite `tests/X.py` → `workspace_name/tests/X.py` when `_STDLIB_SHADOW_DIRS` | Pytest stdlib shadow workaround | environment workaround | medium | medium | encapsulate |

### 3.10 Execution Loop

| File | Function | Behavior | Why | Category | Risk | Impact | Recommendation |
|------|----------|----------|-----|----------|------|--------|----------------|
| `agent/runtime/execution_loop.py` | `_critic_and_retry` | `_Eval` → `diagnose` → `plan_retry` | Retry hints | generic | low | low | keep |
| `agent/runtime/execution_loop.py` | `_apply_hints` | `plan_override` or `rewrite_query` | Apply retry hints | generic | low | low | keep |
| `agent/runtime/execution_loop.py` | `_merge_patch_telemetry` | `_s24_fields` list of telemetry keys | Telemetry | generic | low | low | keep |

### 3.11 Policy Engine

| File | Function | Behavior | Why | Category | Risk | Impact | Recommendation |
|------|----------|----------|-----|----------|------|--------|----------------|
| `agent/execution/policy_engine.py` | `POLICIES` | SEARCH: 5 attempts, EDIT: 2, INFRA: 2; retry_on strings | Retry policy | product policy | low | low | move to config |
| `agent/execution/policy_engine.py` | `classify_result` | String matching on `empty`, `patch`, `edit`, `infra`, `returncode`, `validation`, `timeout`, `tool` | Result classification | generic heuristic | low | low | keep |

### 3.12 Mutation Strategies / Retry Logic

| File | Function | Behavior | Why | Category | Risk | Impact | Recommendation |
|------|----------|----------|-----|----------|------|--------|----------------|
| `agent/execution/mutation_strategies.py` | `generate_query_variants` | Underscorify, strip digits, shorten | Query rewrite | generic heuristic | low | low | keep |
| `agent/execution/mutation_strategies.py` | `symbol_retry` | `file_level`, `symbol_short`, `alternate_target` | Edit retry | generic heuristic | low | low | keep |
| `agent/runtime/retry_guard.py` | `should_retry_strategy` | `syntax_error`, `timeout` → 1 retry; `unknown` → no retry | Retry guard | product policy | medium | medium | move to config |

### 3.13 Harness / Real Execution / Runner

| File | Function | Behavior | Why | Category | Risk | Impact | Recommendation |
|------|----------|----------|-----|----------|------|--------|----------------|
| `tests/agent_eval/harness.py` | `_is_docs_consistency_task` | `tags` contains `docs` and `consistency` | Task routing | benchmark-shaped heuristic | high | High | refactor |
| `tests/agent_eval/harness.py` | `_is_explain_artifact_task` | `grading_mode == "explain_artifact"` | Task routing | benchmark-shaped heuristic | high | High | refactor |
| `tests/agent_eval/harness.py` | `_build_phase_1_steps` | Docs-consistency: SEARCH+EDIT; explain-artifact: SEARCH+EXPLAIN+WRITE_ARTIFACT | Phase steps | benchmark-shaped heuristic | high | High | refactor |
| `tests/agent_eval/harness.py` | `_STDLIB_SHADOW_DIRS` | `logging`, `config`, `parser`, `ast`, `types` | Pytest rewrite | environment workaround | medium | medium | encapsulate |
| `tests/agent_eval/harness.py` | `compute_success` | `structural_loop` → structural_success; `explain_artifact` → explain_ok; else validation_passed | Success logic | grading_mode dependence | high | High | refactor |
| `tests/agent_eval/real_execution.py` | `_compat_plan_dict_for_audit` | Tags `repair`, `feature`, `refactor`, `tests`, `multi_file` → SEARCH+EDIT | Plan injection | benchmark-shaped heuristic | high | High | refactor |
| `tests/agent_eval/real_execution.py` | `_pytest_inner_validation_cmd` | Prefer pytest in validation_commands | Validation cmd | validation-command dependence | medium | medium | keep |

### 3.14 Semantic RCA / Failure Buckets / Observability

| File | Function | Behavior | Why | Category | Risk | Impact | Recommendation |
|------|----------|----------|-----|----------|------|--------|----------------|
| `tests/agent_eval/semantic_rca.py` | `classify_wrong_patch_root_cause` | 20+ cause labels; branches on `gen_reject`, `sem_reject`, `grounded_strategy`, `validation_script_selected`, `likely_stdlib_shadowing`, etc. | RCA classification | benchmark-shaped heuristic | high | High | refactor |
| `tests/agent_eval/failure_buckets.py` | `classify_failure_bucket` | String matching on `index_failed`, `recursionerror`, `validation_tests_failed`, `edit`, `patch`, `retriev`, `empty`, `ranked_context`, `goal_not`, `phase_validation_failed`, `wrong file`, `assert`, `syntax`, `ambiguous` | Failure bucket | benchmark-shaped heuristic | medium | medium | refactor |
| `tests/agent_eval/failure_buckets.py` | `infer_first_failing_stage` | SEARCH / EDIT / VALIDATE based on `structural_success`, `validation_passed`, `patches_applied`, `attempted_target_files`, `viable` | Stage inference | generic heuristic | low | low | keep |
| `tests/agent_eval/workspace_artifacts.py` | `heuristic_unrelated_files` | `.symbol_graph`, `__pycache__`, `.git` | Unrelated files | generic heuristic | low | low | keep |
| `tests/agent_eval/workspace_artifacts.py` | `scan_bad_edit_patterns` | `<<<<<<<`, `>>>>>>>`, `pass` > 8 without `def test` | Bad edit pattern | generic heuristic | low | low | keep |

### 3.15 Repo Indexing / Graph Builder / Dependency Extraction

| File | Function | Behavior | Why | Category | Risk | Impact | Recommendation |
|------|----------|----------|-----|----------|------|--------|----------------|
| `repo_graph/graph_builder.py` | `_resolve_symbol_to_id` | Exact, short name, module.symbol | Symbol resolution | generic heuristic | low | low | keep |
| `repo_graph/graph_builder.py` | `_MAX_SAMPLE_UNRESOLVED = 5` | Log cap | Diagnostics | product policy | low | low | keep |

---

## 4. Benchmark-Shaped Logic Audit

### 4.1 Looks Generic but Probably Benchmark-Shaped

| Location | Behavior | Evidence |
|----------|----------|----------|
| `agent/retrieval/task_semantics.py` | `instruction_suggests_docs_consistency` | Keywords `so scripts/`, `so benchmark_`, `benchmark_local/` are benchmark-specific |
| `editing/grounded_patch_generator.py` | `_try_halve_return_repair` | Only fires for `halve` function; holdout-specific |
| `editing/grounded_patch_generator.py` | `_find_md_version_any_format` | `benchmark_local/` in search_dirs; `VERSION_NOTE.md`, `README_BENCH.md` |
| `editing/patch_generator.py` | `_synthetic_changelog_version_align` | `CHANGELOG.md`, `lib/version.py`; common fixture layout |
| `editing/patch_generator.py` | `_synthetic_api_base_align` | `API.md`, `spec/api_spec.py`; holdout-specific layout |
| `agent/retrieval/target_resolution.py` | `resolve_module_descriptor_to_files` | `version_meta`, `typer_ver`, `readme_bench`; benchmark-specific tokens |

### 4.2 Direct Benchmark Coupling

| Location | Behavior | Evidence |
|----------|----------|----------|
| `editing/patch_generator.py` | `_synthetic_docs_stability_align` | `benchmark_local/DECORATORS_NOTE.md`, `benchmark_local/bench_click_meta.py` |
| `editing/patch_generator.py` | `_synthetic_docs_httpbin_align` | `benchmark_local/HTTPBIN_NOTE.md`, `benchmark_local/bench_requests_meta.py` |
| `editing/patch_generator.py` | `_inject_click_benchmark_multifile_change` | `benchmark_local/part_a.py`, `part_a`, `unified`, `legacy` |
| `editing/patch_generator.py` | `_inject_shared_prefix_multifile` | `pkg_a/constants.py` |
| `tests/agent_eval/harness.py` | `_build_phase_1_steps` | `docs` + `consistency` tags → SEARCH+EDIT; `explain_artifact` → WRITE_ARTIFACT |
| `tests/agent_eval/real_execution.py` | `_compat_plan_dict_for_audit` | Tags `repair`, `feature`, `refactor`, `tests`, `multi_file` → SEARCH+EDIT | 

### 4.3 Suspicious Naming-Pattern Dependence

| Pattern | Where Used | Purpose |
|---------|------------|--------|
| `safe_div` | `patch_generator.py` | Function-name–specific repair |
| `is_valid` | `patch_generator.py` | Function-name–specific repair |
| `enable_debug` | `patch_generator.py` | Function-name–specific repair |
| `log_level` | `patch_generator.py` | Function-name–specific repair |
| `beta_enabled` | `patch_generator.py` | Function-name–specific repair |
| `describe_app` | `patch_generator.py` | Function-name–specific repair |
| `multiply` | `patch_generator.py` | Function-name–specific repair |
| `tokenize` | `patch_generator.py` | Function-name–specific repair |
| `double` | `patch_generator.py` | Function-name–specific repair |
| `halve` | `grounded_patch_generator.py` | Function-name–specific repair |
| `SHARED_PREFIX` | `patch_generator.py` | Constant-name–specific repair |
| `SUFFIX` | `patch_generator.py` | Constant-name–specific repair |

### 4.4 Validation-Command Dependence

| Location | Behavior |
|----------|----------|
| `agent/tools/validation_scope.py` | `benchmark_local/` in cmd → `benchmark_local` scope |
| `tests/agent_eval/real_execution.py` | `_pytest_inner_validation_cmd` prefers pytest |
| `agent/retrieval/target_resolution.py` | `validation_script_paths_from_command` extracts `bin/assert_*.py`, `scripts/check_*.py` from cmd |

### 4.5 Fixture/repo-Layout Dependence

| Layout | Where Assumed |
|--------|---------------|
| `benchmark_local/` | `target_resolution.py`, `grounded_patch_generator.py`, `patch_generator.py`, `validation_scope.py` |
| `lib/version.py` | `patch_generator.py` |
| `spec/api_spec.py` | `patch_generator.py` |
| `pkg_a/constants.py` | `patch_generator.py` |
| `tests/` | `target_resolution.py`, `harness.py` (`_transform_pytest_cmd_for_shadowing`) |

---

## 5. Pattern Families

### 5.1 Source-vs-Validator File Selection Rules

| Implementation Site | Overlap |
|---------------------|---------|
| `target_resolution.py`: `is_validation_script_path`, `rank_edit_targets` | Primary |
| `task_semantics.py`: `validation_check_script_paths_in_instruction`, `instruction_asks_to_modify_validation_script` | Shared |
| `diff_planner.py`: Uses `resolve_edit_targets_for_plan` | Consumer |

**Proposal:** Single `ValidationScriptClassifier` in `target_resolution`; task_semantics uses it.

### 5.2 Docs-Consistency Alignment Rules

| Implementation Site | Overlap |
|---------------------|---------|
| `target_resolution.py`: `resolve_module_descriptor_to_files` (version_meta, typer_ver) | Duplicated |
| `patch_generator.py`: `_synthetic_docs_version_align`, `_synthetic_docs_stability_align`, `_synthetic_docs_httpbin_align` | Benchmark-specific |
| `grounded_patch_generator.py`: `_try_version_constant_align`, `_try_url_constant_align` | Generic |
| `task_semantics.py`: `instruction_suggests_docs_consistency` | Shared |
| `diff_planner.py`: `_instruction_hint_file_targets` (version + constants) | Benchmark-specific |

**Proposal:** Remove benchmark-specific docs align; keep only generic version/URL align in grounded_patch_generator.

### 5.3 Explain-Artifact Routing Rules

| Implementation Site | Overlap |
|---------------------|---------|
| `harness.py`: `_is_explain_artifact_task`, `_build_phase_1_steps` | Harness-only |
| `harness.py`: `compute_success` | Harness-only |
| `harness.py`: `explain_artifact_ok` | Harness-only |
| `real_execution.py`: `offline_llm_stubs` with `explain_required_substrings` | Stub |

**Proposal:** Keep in harness; it is benchmark plumbing. Do not leak into production agent.

### 5.4 Grounded Patch Strategy Patterns

| Implementation Site | Overlap |
|---------------------|---------|
| `grounded_patch_generator.py`: `_try_*` strategies | Primary |
| `patch_generator.py`: `_synthetic_*`, `_generic_*` | Fallback |

**Proposal:** Remove `_synthetic_*` that hard-code function names; keep `_generic_*`; grounded layer is primary.

### 5.5 Path Hint Extraction Rules

| Implementation Site | Overlap |
|---------------------|---------|
| `task_semantics.py`: `instruction_path_hints`, `instruction_edit_target_paths` | Primary |
| `patch_generator.py`: `_instruction_py_hints` | Duplicated |
| `target_resolution.py`: `validation_script_paths_from_instruction` | Shared |

**Proposal:** Consolidate in `task_semantics`; patch_generator imports from there.

### 5.6 Semantic Ranking Rules

| Implementation Site | Overlap |
|---------------------|---------|
| `grounded_patch_generator.py`: `_apply_semantic_ranking` | Primary |

**Proposal:** Keep; generic enough.

### 5.7 Stdlib Shadowing Workarounds

| Implementation Site | Overlap |
|---------------------|---------|
| `target_resolution.py`: `_STDLIB_SHADOW_CANDIDATES` | Telemetry |
| `harness.py`: `_STDLIB_SHADOW_DIRS`, `_transform_pytest_cmd_for_shadowing` | Pytest rewrite |

**Contradiction:** `target_resolution` has `io`; `harness` has `logging`, `config`, `parser`, `ast`, `types` (no `io` — "io/ cannot be fixed").

**Proposal:** Single config list; document that `io` is special.

### 5.8 Pytest Command Rewriting

| Implementation Site | Overlap |
|---------------------|---------|
| `harness.py`: `_transform_pytest_cmd_for_shadowing` | Harness-only |

**Proposal:** Keep in harness; it is benchmark plumbing.

### 5.9 Graph Symbol Resolution Fallbacks

| Implementation Site | Overlap |
|---------------------|---------|
| `repo_graph/graph_builder.py`: `_resolve_symbol_to_id` | Primary |

**Proposal:** Keep; generic.

### 5.10 Retry Mutation Variants

| Implementation Site | Overlap |
|---------------------|---------|
| `mutation_strategies.py`: `symbol_retry` | Primary |
| `policy_engine.py`: `POLICIES` | Primary |

**Proposal:** Keep; move to config.

### 5.11 Failure Bucket / RCA Inference Rules

| Implementation Site | Overlap |
|---------------------|---------|
| `failure_buckets.py`: `classify_failure_bucket` | Primary |
| `semantic_rca.py`: `classify_wrong_patch_root_cause` | Primary |

**Proposal:** Refactor; reduce string matching; use structured telemetry.

---

## 6. Hard-Coded Strings and Pattern Matching Audit

### 6.1 Exact Strings / Regex Table

| String or Regex | Where Used | Purpose | Generic |
|-----------------|------------|---------|---------|
| `scripts/assert_\w+\.py` | `target_resolution.py` | Validation script pattern | Yes |
| `scripts/check_\w+\.py` | `target_resolution.py` | Validation script pattern | Yes |
| `scripts/verify_\w+\.py` | `target_resolution.py` | Validation script pattern | Yes |
| `bin/assert_\w+\.py` | `target_resolution.py` | Validation script pattern | Yes |
| `benchmark_local/` | `target_resolution.py`, `grounded_patch_generator.py`, `patch_generator.py`, `validation_scope.py` | Path hint | No |
| `version_meta` | `target_resolution.py` | Module descriptor | No |
| `typer_ver` | `target_resolution.py` | Module descriptor | No |
| `readme_bench` | `target_resolution.py` | Module descriptor | No |
| `VERSION_NOTE.md` | `grounded_patch_generator.py` | Version finder | No |
| `README_BENCH.md` | `grounded_patch_generator.py` | Version finder | No |
| `benchmark_local/version_meta.py` | `target_resolution.py` | Path hint | No |
| `benchmark_local/typer_ver.py` | `target_resolution.py` | Path hint | No |
| `benchmark_local/DECORATORS_NOTE.md` | `patch_generator.py` | Docs stability | No |
| `benchmark_local/bench_click_meta.py` | `patch_generator.py` | Docs stability | No |
| `benchmark_local/HTTPBIN_NOTE.md` | `patch_generator.py` | Docs httpbin | No |
| `benchmark_local/bench_requests_meta.py` | `patch_generator.py` | Docs httpbin | No |
| `benchmark_local/part_a.py` | `patch_generator.py` | Multifile inject | No |
| `pkg_a/constants.py` | `patch_generator.py` | Multifile inject | No |
| `safe_div` | `patch_generator.py` | Function-name repair | No |
| `is_valid` | `patch_generator.py` | Function-name repair | No |
| `enable_debug` | `patch_generator.py` | Function-name repair | No |
| `log_level` | `patch_generator.py` | Function-name repair | No |
| `beta_enabled` | `patch_generator.py` | Function-name repair | No |
| `describe_app` | `patch_generator.py` | Function-name repair | No |
| `multiply` | `patch_generator.py` | Function-name repair | No |
| `tokenize` | `patch_generator.py` | Function-name repair | No |
| `double` | `patch_generator.py` | Function-name repair | No |
| `halve` | `grounded_patch_generator.py` | Function-name repair | No |
| `SHARED_PREFIX` | `patch_generator.py` | Constant repair | No |
| `SUFFIX` | `patch_generator.py` | Constant repair | No |
| `CLICK_BENCH_API_STABILITY` | `patch_generator.py` | Docs stability | No |
| `DEFAULT_HTTPBIN_BASE` | `patch_generator.py` | Docs httpbin | No |
| `APP_VERSION` | `patch_generator.py` | Docs version | No |
| `RELEASE_VERSION` | `patch_generator.py` | Changelog align | No |
| `API_BASE` | `patch_generator.py` | API align | No |
| `io`, `logging`, `config`, `parser`, `ast`, `types` | `target_resolution.py`, `harness.py` | Stdlib shadow | No (io special) |

### 6.2 Shortlist of Likely Duplicate Logic

- `instruction_path_hints` (task_semantics) vs `_instruction_py_hints` (patch_generator)
- `_STDLIB_SHADOW_CANDIDATES` (target_resolution) vs `_STDLIB_SHADOW_DIRS` (harness)
- `docs-consistency` detection in task_semantics, diff_planner, patch_generator, harness

### 6.3 Shortlist of Hidden Environment Assumptions

- `benchmark_local/` directory exists in fixtures
- `validation_commands` include pytest or check scripts
- `grading_mode` and `orchestration_path` set by task specs
- `AUTOSTUDIO_INNER_VALIDATION_CMD` env var set by real_execution

---

## 7. Decision Logic Audit

### 7.1 Edit Target Selection

| Step | Deterministic | Thresholds | Tiebreakers | Evidence-Based |
|------|---------------|------------|-------------|----------------|
| `resolve_edit_targets_for_plan` | Yes | Penalties 0–100 | Sort by penalty | No |
| `rank_edit_targets` | Yes | Penalty < 80 = good targets | resolution_penalty, edit_target_miss, hint_miss | No |
| `_instruction_hint_file_targets` | Yes | docs-consistency: py vs md order | version+constants vs else | No |

### 7.2 Grounded Candidate Creation and Rejection

| Step | Deterministic | Thresholds | Tiebreakers | Evidence-Based |
|------|---------------|------------|-------------|----------------|
| `generate_grounded_candidates` | Yes | MAX_CANDIDATES=4 | Strategy order | No |
| `_apply_semantic_ranking` | Yes | semantic_match_score 0–3 | rank, -score | No |
| `validate_semantic_grounded_candidate` | Yes | Regex on instruction | — | No |

### 7.3 Validation Command Resolution

| Step | Deterministic | Thresholds | Tiebreakers | Evidence-Based |
|------|---------------|------------|-------------|----------------|
| `resolve_inner_loop_validation` | Yes | ctx or env | — | No |
| `_pytest_inner_validation_cmd` | Yes | Prefer pytest | First cmd | No |

### 7.4 Retry Mutation

| Step | Deterministic | Thresholds | Tiebreakers | Evidence-Based |
|------|---------------|------------|-------------|----------------|
| `symbol_retry` | Yes | — | file_level, symbol_short, alternate_target | No |
| `should_retry_strategy` | Yes | syntax_error/timeout: 1 retry | — | No |

### 7.5 Failure Bucketing / Labeling

| Step | Deterministic | Thresholds | Tiebreakers | Evidence-Based |
|------|---------------|------------|-------------|----------------|
| `classify_failure_bucket` | Yes | String matching on notes, error | Order of branches | No |
| `classify_wrong_patch_root_cause` | Yes | Telemetry field matching | Order of branches | No |

### 7.6 Structural Success Inference

| Step | Deterministic | Thresholds | Tiebreakers | Evidence-Based |
|------|---------------|------------|-------------|----------------|
| `_task_success` | Yes | `parent_goal_met` or `len(errors)==0` | orchestration_path | No |

---

## 8. Drift Assessment

### Where Has the Code Drifted Most?

- **Patch generator:** `_synthetic_repair` and its callers are the most overfit. Many branches exist only to pass holdout8, adversarial12, and docs-consistency tasks.
- **Target resolution:** `resolve_module_descriptor_to_files` encodes benchmark-specific tokens (`version_meta`, `typer_ver`, `readme_bench`, `benchmark_local/`).
- **Task semantics:** `instruction_suggests_docs_consistency` includes `benchmark_local/`, `so benchmark_` which are benchmark-specific.
- **Harness:** `grading_mode`, `orchestration_path`, `tags` drive routing; this is expected for benchmark plumbing but should not leak into production.

### Which Layers Are Most Overfit?

1. **Patch generator fallback** — High
2. **Target resolution module descriptor** — High
3. **Task semantics docs-consistency** — High
4. **Harness phase building** — High (benchmark-only)
5. **Semantic RCA** — Medium (telemetry-shaped)

### Which Heuristics Are Actually Necessary Product Behavior?

- Validation script demotion
- Patch effectiveness gates
- Forbidden path patterns
- Symbol retry mutation
- Instruction path hints

### Which Should Be First to Delete or Replace?

1. `_synthetic_docs_stability_align`, `_synthetic_docs_httpbin_align`, `_inject_click_benchmark_multifile_change`, `_inject_shared_prefix_multifile`
2. `_synthetic_safe_div_repair`, `_synthetic_is_valid_repair`, `_synthetic_enable_debug`, `_synthetic_log_level`, `_synthetic_shared_prefix_rename`
3. Function-name–specific inline branches in `_synthetic_repair` (`multiply`, `tokenize`, `double`, `beta_enabled`, `describe_app`, `part_a`/`SUFFIX`)
4. `version_meta`, `typer_ver`, `readme_bench`, `benchmark_local/` in `resolve_module_descriptor_to_files`
5. `_try_halve_return_repair` in grounded_patch_generator

### Which Should Be Converted to Configuration or Policy Modules?

- `POLICIES` (max_attempts, retry_on)
- `should_retry_strategy` failure types
- `_STDLIB_SHADOW_CANDIDATES` / `_STDLIB_SHADOW_DIRS`
- `MAX_RANKED_TARGETS`, `MAX_FILES_EDITED`, `MAX_PATCH_LINES`

### Which Should Be Replaced with Evidence-Driven or Model-Driven Logic?

- `classify_failure_bucket` — could use structured telemetry + model
- `classify_wrong_patch_root_cause` — could use structured telemetry + model
- `resolve_module_descriptor_to_files` — could use retrieval + model

---

## 9. Cleanup Plan

### Immediate Removals

| Item | Risk | Regression |
|------|------|------------|
| `_synthetic_docs_stability_align` | Low | Holdout docs stability task |
| `_synthetic_docs_httpbin_align` | Low | Holdout httpbin task |
| `_inject_click_benchmark_multifile_change` | Low | Adversarial multifile task |
| `_inject_shared_prefix_multifile` | Low | Holdout shared prefix task |
| `_synthetic_safe_div_repair` | Low | Holdout safe_div task |
| `_synthetic_is_valid_repair` | Low | Holdout is_valid task |
| `_synthetic_enable_debug` | Low | Holdout enable_debug task |
| `_synthetic_log_level` | Low | Holdout log_level task |
| `_synthetic_shared_prefix_rename` | Low | Holdout shared prefix task |
| Inline `multiply`, `tokenize`, `double`, `beta_enabled`, `describe_app`, `part_a`/`SUFFIX` | Low | Various holdout/adversarial tasks |
| `_try_halve_return_repair` | Low | Holdout halve task |

### Near-Term Refactors

| Item | Risk | Regression |
|------|------|------------|
| `resolve_module_descriptor_to_files` — remove version_meta, typer_ver, readme_bench, benchmark_local paths | Medium | Docs-consistency tasks |
| `_synthetic_docs_version_align` — generalize or remove | Medium | Docs version task |
| `_synthetic_changelog_version_align` — generalize | Medium | Changelog task |
| `_synthetic_api_base_align` — generalize | Medium | API align task |
| `instruction_suggests_docs_consistency` — remove `benchmark_local/`, `so benchmark_` | Low | Docs-consistency routing |
| `_instruction_hint_file_targets` — remove version+constants special case | Medium | Docs-consistency tasks |

### Keep but Encapsulate

| Item | Action |
|------|--------|
| `_STDLIB_SHADOW_CANDIDATES` / `_STDLIB_SHADOW_DIRS` | Single config module |
| `_transform_pytest_cmd_for_shadowing` | Keep in harness; document |
| `classify_failure_bucket` | Reduce string matching; use structured telemetry |
| `classify_wrong_patch_root_cause` | Reduce string matching; use structured telemetry |

### Move to Configuration

| Item | Config Key |
|------|------------|
| `POLICIES` | `agent.policy.max_attempts`, `agent.policy.retry_on` |
| `should_retry_strategy` | `agent.retry.failure_types` |
| `MAX_RANKED_TARGETS` | `target_resolution.max_ranked_targets` |
| `_STDLIB_SHADOW_*` | `stdlib_shadow_modules` |

### Replace with Generalized Evidence-Based Logic

| Item | Replacement |
|------|-------------|
| `resolve_module_descriptor_to_files` | Retrieval + instruction parsing; no hard-coded tokens |
| Function-name–specific repairs | Grounded layer only; no synthetic fallbacks |

### Add Tests Before Touching

| Item | Test |
|------|------|
| All removals | Regression tests on holdout8, adversarial12, external6 |
| `resolve_module_descriptor_to_files` refactor | Unit tests for generic module descriptors |

---

## 10. Appendices

### Appendix A: File-by-File Inventory

| File | Heuristic Count | Benchmark-Shaped |
|------|-----------------|------------------|
| `agent/retrieval/target_resolution.py` | 8 | 4 |
| `agent/retrieval/task_semantics.py` | 5 | 2 |
| `editing/diff_planner.py` | 3 | 2 |
| `editing/grounded_patch_generator.py` | 12 | 2 |
| `editing/patch_generator.py` | 25 | 18 |
| `editing/patch_executor.py` | 3 | 0 |
| `editing/patch_effectiveness.py` | 2 | 0 |
| `agent/execution/mutation_strategies.py` | 2 | 0 |
| `agent/execution/policy_engine.py` | 3 | 0 |
| `agent/runtime/retry_guard.py` | 1 | 0 |
| `agent/runtime/execution_loop.py` | 2 | 0 |
| `agent/tools/validation_scope.py` | 1 | 0 |
| `tests/agent_eval/harness.py` | 8 | 6 |
| `tests/agent_eval/real_execution.py` | 4 | 2 |
| `tests/agent_eval/semantic_rca.py` | 1 | 1 |
| `tests/agent_eval/failure_buckets.py` | 2 | 1 |
| `tests/agent_eval/workspace_artifacts.py` | 2 | 0 |
| `repo_graph/graph_builder.py` | 1 | 0 |

### Appendix B: Regex/String Inventory

See Section 6.1.

### Appendix C: Shortlist of Likely Duplicate Logic

See Section 6.2.

### Appendix D: Shortlist of Hidden Environment Assumptions

See Section 6.3.
