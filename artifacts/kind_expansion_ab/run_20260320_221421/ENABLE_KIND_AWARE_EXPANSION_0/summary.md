# Agent eval run: `search_stack`

- **Run directory:** `/Users/shang/my_work/AutoStudio/artifacts/kind_expansion_ab/run_20260320_221421/ENABLE_KIND_AWARE_EXPANSION_0`
- **Execution mode:** `offline`
- **Duration (wall):** 19.66s
- **Tasks:** 6

| task_id | success | validation_passed | structural_success | failure_bucket | first_failing_stage | attempts | retries | replans |
|---------|---------|-------------------|--------------------|---------------|---------------------|----------|---------|---------|
| sq_entrypoint_arch | True | True | True | None | None | - | - | 0 |
| sq_symbol_exact | True | True | True | None | None | - | - | 0 |
| sq_fallback_guard | True | True | True | None | None | - | - | 0 |
| sq_impl_not_tests | True | True | True | None | None | - | - | 0 |
| sq_2hop_arch | True | True | True | None | None | - | - | 0 |
| sq_config_settings | True | True | True | None | None | - | - | 0 |

**Overall success:** 6/6

## Aggregates
- attempts_total_aggregate: 0
- retries_used_aggregate: 0
- replans_used_aggregate: 0

## Integrity (Stage 31)
- execution_mode: offline
- run_valid_for_live_eval: False
- invalid_live_model_task_count: 0
- zero_model_call_task_count: 6
- offline_stubbed_task_count: 6
- explain_stubbed_task_count: 0
- plan_injection_task_count: 6
- model_call_count_total: 0
- small_model_call_count_total: 0
- reasoning_model_call_count_total: 0

## Histograms
- failure_bucket: {}
- patch_reject_reason: {}
- validation_scope_kind: {}
- first_failing_stage: {}
