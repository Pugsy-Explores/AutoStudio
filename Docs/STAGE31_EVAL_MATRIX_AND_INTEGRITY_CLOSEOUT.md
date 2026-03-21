# Stage 31 — Evaluation Matrix and Integrity Closeout

## Summary

Stage 31 delivers a clean evaluation matrix across execution modes, suite-level integrity aggregation, and explicit separation between benchmark plumbing and core agent behavior. No score chasing; this is an honest post-decontamination evaluation and test-architecture cleanup.

---

## 1. Execution Modes

| Mode | Description | Model Calls | Stubs | Plan Injection |
|------|-------------|-------------|-------|----------------|
| **mocked** | Stub execution_loop; no real agent run | 0 | No (execution_loop mocked) | No |
| **offline** | Real execution_loop + offline_llm_stubs + plan injection | 0 | Yes | Yes |
| **live_model** | Real execution_loop + real model client | ≥1 required | No | No |

**Deprecated:** `--real` maps to `offline` with deprecation warning.

---

## 2. Task Artifact Integrity Fields

Every task outcome (`outcome.json` `_audit`) now includes:

| Field | Type | Description |
|-------|------|-------------|
| `execution_mode` | str | mocked \| offline \| live_model |
| `model_call_count` | int | Per-task model calls |
| `small_model_call_count` | int | Per-task small-model calls |
| `reasoning_model_call_count` | int | Per-task reasoning-model calls |
| `used_offline_stubs` | bool | True if offline_llm_stubs was used |
| `used_plan_injection` | bool | True if benchmark plan injection was used |
| `used_explain_stub` | bool | True if explain step used stub output |
| `integrity_valid` | bool | True only when live_model + real model calls + no stubs |
| `integrity_failure_reason` | str \| null | Reason when integrity_valid is False |

---

## 3. Suite-Level Aggregation

Runner summary (`summary.json`) now includes:

| Field | Description |
|-------|-------------|
| `invalid_live_model_task_count` | Live-model tasks with integrity_valid=False |
| `zero_model_call_task_count` | Tasks with model_call_count=0 |
| `offline_stubbed_task_count` | Tasks that used offline_llm_stubs |
| `explain_stubbed_task_count` | Tasks that used explain stub |
| `plan_injection_task_count` | Tasks that used plan injection |
| `model_call_count_total` | Sum of model_call_count across tasks |
| `small_model_call_count_total` | Sum of small_model_call_count |
| `reasoning_model_call_count_total` | Sum of reasoning_model_call_count |

---

## 4. Honest Evaluation Matrix (Post-Decontamination)

### Sample Run: audit6, single task (core12_mini_repair_calc)

| Metric | Mocked | Offline | Live-Model |
|--------|--------|---------|------------|
| execution_mode | mocked | offline | live_model |
| model_call_count_total | 0 | 0 | ≥1 (when valid) |
| used_offline_stubs | false | true | false |
| used_plan_injection | false | true | false |
| integrity_valid | false | false | true (when model called) |
| run_valid_for_live_eval | false | false | true (when all tasks valid) |

### What Still Fails After Decontamination

**Offline mode (audit6, core12_mini_repair_calc):**

- **Failure:** `edit_grounding_failure` / `weakly_grounded_patch`
- **Root cause:** No benchmark-specific synthetic repairs (Stage 30 removed `_inject_click_benchmark_multifile_change`, `_synthetic_changelog_version_align`, etc.). The agent relies on grounded patch generation; when the plan text is vague or the rewriter returns `{"steps": []}`, no grounded candidate is found.
- **Expected:** This is honest. Offline regression tests now measure real agent behavior without fixture-aware shortcuts.

**Live-model mode:**

- Requires configured hosted model. With `_call_chat` mocked for CI, `integrity_valid` can be true when model_client records calls.
- Zero model calls → `integrity_failure_reason: "zero_real_model_calls"` → `integrity_valid: false`.

---

## 5. Benchmark Plumbing vs Core Agent

| Layer | Responsibility | Benchmark-Specific? |
|-------|-----------------|--------------------|
| **Harness** (`harness.py`) | Index workspace, run structural agent, validate, compute outcome | No — mode dispatch only |
| **Real execution** (`real_execution.py`) | offline_llm_stubs, plan injection, live-model path | Yes — stubs and plan injection are benchmark scaffolding |
| **Runner** (`runner.py`) | Load suites, run tasks, aggregate, write artifacts | No — suite names are config |
| **Model client** (`model_client.py`) | Model call audit (telemetry) | No — audit is generic |
| **Core agent** (planner, retrieval, editing) | Planning, retrieval, patch generation | No — Stage 30 removed benchmark heuristics |

**Explicit separation:** Benchmark plumbing lives in `tests/agent_eval/` (harness, real_execution, runner). Core agent code in `agent/`, `editing/`, `planner/` has no benchmark-specific branches (post Stage 30).

---

## 6. Files Changed

| File | Changes |
|------|---------|
| `tests/agent_eval/harness.py` | `_ensure_integrity_fields()` for mocked mode; `integrity_valid` in extra |
| `tests/agent_eval/runner.py` | Integrity fields in `_audit`; suite-level aggregation; `audit6` in `_load_suite`; live_model in suite-loading branch; markdown summary integrity section |
| `tests/agent_eval/compare_modes.py` | **New** — Compact comparison utility for offline vs live_model |
| `tests/agent_eval/test_stage31_eval_matrix.py` | **New** — 8 regression tests for eval matrix and integrity |

---

## 7. Usage

### Run evaluation matrix

```bash
# Mocked (deterministic, no network)
python3 -m tests.agent_eval.runner --suite audit6 --execution-mode mocked

# Offline (stubs + plan injection)
python3 -m tests.agent_eval.runner --suite audit6 --execution-mode offline

# Live-model (real model required)
python3 -m tests.agent_eval.runner --suite live4 --execution-mode live_model
```

### Compare offline vs live

```bash
python3 -m tests.agent_eval.compare_modes \
  artifacts/agent_eval_runs/offline_run/summary.json \
  artifacts/agent_eval_runs/live_run/summary.json
```

---

## 8. Regression Tests

- `test_mocked_mode_deterministic` — Mocked has no model calls, integrity_valid=False
- `test_offline_mode_uses_stubs_and_plan_injection` — Offline sets used_offline_stubs, used_plan_injection
- `test_live_model_mode_never_uses_offline_stubs` — Live-model never uses stubs
- `test_live_model_zero_model_calls_invalid` — Zero model calls → integrity_failure_reason
- `test_deprecated_real_maps_to_offline` — --real maps to offline
- `test_artifact_integrity_fields_always_written` — _audit always has integrity fields
- `test_summary_integrity_aggregation_fields` — Summary has suite-level aggregation
- `test_compare_modes_utility` — compare_modes produces valid output

---

## 9. Production Impact

- **Unchanged** except for model call audit telemetry in `model_client.py` (already present from Stage 28).
- No benchmark heuristics, synthetics, or fixture shortcuts added.
