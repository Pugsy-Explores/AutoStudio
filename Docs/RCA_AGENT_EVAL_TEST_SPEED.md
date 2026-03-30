# RCA: agent_eval Test Suite Speed

**Date:** 2025-03-20  
**Command:** `python3 -m pytest tests/agent_eval -q`  
**Baseline:** 58 passed in **267s** (~4.5 min)

---

## Executive Summary

~96% of runtime is spent in **4 tests** that each run the full `core12` suite (12 tasks). Three of these tests validate the same runner output and can share a single `run_suite` invocation. One test only needs one task's artifact schema.

| Optimization | Est. savings | Effort |
|--------------|--------------|--------|
| Shared `run_suite` fixture for 3 tests | ~130s | Medium |
| `task_filter` for schema-only test | ~57s | Low |
| Index caching (optional) | ~30s | High |
| **Total potential** | **~190s (70%)** | |

---

## Root Cause: Duplicate Full-Suite Runs

### Slowest Tests (from `--durations=0`)

| Duration | Test |
|----------|------|
| 69.44s | `test_stage12_1.py::test_run_suite_mocked_backward_compat` |
| 64.57s | `test_benchmark_smoke.py::test_runner_writes_summary` |
| 63.26s | `test_stage14_benchmark_infra.py::test_artifact_schema_per_task` |
| 60.00s | `test_stage14_benchmark_infra.py::test_runner_summary_aggregation` |
| 9.63s | `test_benchmark_smoke.py::test_run_single_task_mocked_smoke` |

**Top 4 = 257s** (96% of total).

### What Each Test Does

All four call `run_suite("core12", ...)`, which:

1. Copies 12 fixture workspaces
2. For each task: `index_workspace()` (tree-sitter, symbol graph, SQLite)
3. For each task: `run_single_task()` → mocked `run_hierarchical` + validation
4. Writes artifacts (outcome.json, summary.json, etc.)

**Per-task cost:** ~5–6s (indexing + execution + I/O).

### Assertion Requirements

| Test | Needs | Can use |
|------|-------|---------|
| `test_run_suite_mocked_backward_compat` | `total_tasks==12`, execution_mode | Full 12-task run |
| `test_runner_writes_summary` | `len(results)==12`, summary.json, summary.md | Full 12-task run |
| `test_runner_summary_aggregation` | `len(per_task_outcomes)==12`, aggregate keys | Full 12-task run |
| `test_artifact_schema_per_task` | First `outcome.json` schema | **1 task only** |

---

## Recommendations

### 1. Shared Session Fixture (High Impact)

**Idea:** Run `run_suite("core12", ...)` once per session; reuse `(run_dir, results, summary)` for the three tests that need full output.

**Implementation:**
- Add `@pytest.fixture(scope="session")` that runs `run_suite` and yields `(run_dir, results, summary)`.
- Use a session-scoped `tmp_path_factory` for the run directory (or a dedicated temp dir).
- `test_run_suite_mocked_backward_compat`, `test_runner_writes_summary`, `test_runner_summary_aggregation` depend on this fixture instead of calling `run_suite` themselves.

**Caveat:** `test_run_suite_mocked_backward_compat` monkeypatches `chdir(tmp_path)` and copies fixtures. The shared fixture must do the same setup. All three tests use the same `tmp_path` + fixture copy pattern, so a single setup works.

**Est. savings:** 2 × 65s ≈ **130s** (3 runs → 1 run).

---

### 2. Use `task_filter` for Schema-Only Test (Low Effort)

**Idea:** `test_artifact_schema_per_task` only reads `first_task = next(Path(run_dir).glob("tasks/*/outcome.json"))`. It does not assert on `len(results)` or `total_tasks`. Run with `task_filter="core12_mini_repair_calc"` (or any single task).

**Implementation:**
```python
run_dir, results, summary = rmod.run_suite(
    "core12",
    Path("artifacts/agent_eval_runs/latest"),
    repo_root=tmp_path,
    task_filter="core12_mini_repair_calc",  # 1 task only
)
```

**Est. savings:** 63s → ~6s ≈ **57s**.

---

### 3. Optional: Index Caching

**Idea:** `index_workspace()` runs for every task. Fixtures are immutable; indexing the same `mini_repos/mr01_arch` produces the same output. Cache by `(workspace_path, fixture_hash)`.

**Effort:** High (cache invalidation, disk layout). Only pursue if 1+2 are insufficient.

---

### 4. Optional: pytest-xdist (Parallel Tests)

**Idea:** `pytest -n auto` runs test files in parallel. The four slow tests would still each run their own `run_suite` unless combined with the shared fixture. Do **after** implementing the shared fixture.

---

## Implementation Order

1. **Phase 1 (quick win):** Add `task_filter` to `test_artifact_schema_per_task` → ~57s saved.
2. **Phase 2 (main win):** Introduce shared `run_suite` fixture; refactor the three full-suite tests to use it → ~130s saved.
3. **Phase 3 (optional):** Add `pytest -n 2` or `-n 4` for remaining parallelism.

**Expected result:** 267s → ~80s (70% faster).

---

## Files to Modify

| File | Change |
|------|--------|
| `tests/agent_eval/test_stage14_benchmark_infra.py` | Add `task_filter` to `test_artifact_schema_per_task`; add shared fixture + refactor `test_runner_summary_aggregation` |
| `tests/agent_eval/test_benchmark_smoke.py` | Use shared fixture for `test_runner_writes_summary` |
| `tests/agent_eval/test_stage12_1.py` | Use shared fixture for `test_run_suite_mocked_backward_compat` |
| `tests/agent_eval/conftest.py` (new or existing) | Define `run_suite_core12_mocked` session fixture |

---

## Verification

After changes:
```bash
python3 -m pytest tests/agent_eval -q --durations=0
```
Target: total < 90s, with the four previously-slow tests showing one ~65s run and three near-instant (fixture reuse).

---

## Implementation Status (Complete)

**Verified:** 2025-03-20

- **Shared fixture:** `run_suite_core12_mocked` in conftest.py; `test_runner_summary_aggregation`, `test_runner_writes_summary`, `test_run_suite_mocked_backward_compat` use it.
- **task_filter:** `test_artifact_schema_per_task` uses `task_filter="core12_mini_repair_calc"` (1 task).
- **Result:** 267s → ~77s (71% faster). Optional: `pytest -n auto` for extra parallelism.
