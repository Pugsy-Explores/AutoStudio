# Agent eval run: `search_stack`

- **Run directory:** `/Users/shang/my_work/AutoStudio/artifacts/bundle_selector_ab/run_20260320_235916/ENABLE_LLM_BUNDLE_SELECTOR_1`
- **Execution mode:** `live_model`
- **Duration (wall):** 1023.46s
- **Tasks:** 6

| task_id | success | validation_passed | structural_success | failure_bucket | first_failing_stage | attempts | retries | replans |
|---------|---------|-------------------|--------------------|---------------|---------------------|----------|---------|---------|
| sq_hard_entrypoint_settings | True | True | True | None | None | - | - | 0 |
| sq_hard_config_runtime | True | True | True | None | None | - | - | 0 |
| sq_hard_fallback_callers | True | True | False | None | None | - | - | 0 |
| sq_hard_dispatch_executor | True | True | True | None | None | - | - | 0 |
| sq_hard_impl_not_tests | True | True | True | None | None | - | - | 0 |
| sq_hard_2hop_arch | True | True | True | None | None | - | - | 0 |

**Overall success:** 6/6

## Aggregates
- attempts_total_aggregate: 0
- retries_used_aggregate: 0
- replans_used_aggregate: 0

## Integrity (Stage 31)
- execution_mode: live_model
- run_valid_for_live_eval: True
- invalid_live_model_task_count: 0
- zero_model_call_task_count: 0
- offline_stubbed_task_count: 0
- explain_stubbed_task_count: 0
- plan_injection_task_count: 0
- model_call_count_total: 90
- small_model_call_count_total: 12
- reasoning_model_call_count_total: 78

## Histograms
- failure_bucket: {}
- patch_reject_reason: {}
- validation_scope_kind: {}
- first_failing_stage: {}
