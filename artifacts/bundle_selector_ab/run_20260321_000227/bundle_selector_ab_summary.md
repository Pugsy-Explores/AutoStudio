# ENABLE_LLM_BUNDLE_SELECTOR — offline A/B

**Verdict:** `canary_worthy`
**Selector quality verdict:** `regress_linking`
**Selector decision source:** `stub` | **Confidence:** `low`
**quality verdict is directional only**

| Metric | OFF | ON | Δ (ON − OFF) |
|--------|-----|----|--------------|
| task_count | 6 | 6 | — |
| bundle_selector_usage_rate | 0 | 0.6667 | 0.6667 |
| average_selected_id_count | 0 | 2.6667 | 2.6667 |
| average_selected_impl_body_count | 0 | 2.6667 | 2.6667 |
| average_selected_linked_row_count | 0 | 0 | 0 |
| average_selected_test_row_count | 0 | 0 | 0 |
| selected_rows_only_rate | 0 | 0.6667 | 0.6667 |
| replanner_trigger_rate | 0 | 0 | 0 |
| architecture_ok_rate | 0.3333 | 0.3333 | 0 |

## Selector quality
- Impl retention rate: 0.9333
- Linked retention rate: 0
- Test drift rate: 0
- Architecture answer ready rate: 0.3333
- Selector vs baseline context delta: -2
- Architecture tasks lost all links rate: 0.6667
- Useful compaction rate: 1
- Selector integrity rate: 1
- Verdict: `regress_linking`

## Interpretation
- Selector usage rate: 0.6667
- Avg selected IDs: 2.6667
- Impl-body retention: 2.6667
- Linked-row retention (architecture): 0
- Replanner trigger rate delta: 0

- OFF run: `/Users/shang/my_work/AutoStudio/artifacts/bundle_selector_ab/run_20260321_000227/ENABLE_LLM_BUNDLE_SELECTOR_0`
- ON run: `/Users/shang/my_work/AutoStudio/artifacts/bundle_selector_ab/run_20260321_000227/ENABLE_LLM_BUNDLE_SELECTOR_1`
- Machine-readable: `/Users/shang/my_work/AutoStudio/artifacts/bundle_selector_ab/run_20260321_000227/bundle_selector_ab_comparison.json`

## Selector-hard slice

**Hard-slice verdict:** `canary_worthy` | **Quality:** `regress_linking`

| task_id | ctx_off | ctx_on | sel_on | linked_ret | impl_ret | arch_ready_off | arch_ready_on | replanner_off | replanner_on |
|---------|---------|--------|--------|------------|----------|----------------|---------------|---------------|--------------|
| sq_hard_2hop_arch | 6 | 4 | 4 | 0 | 0.8 | True | False | False | False |
| sq_hard_config_runtime | 6 | 4 | 4 | 0 | 1.3333 | True | False | False | False |
| sq_hard_dispatch_executor | 6 | 4 | 4 | 0 | 0.8 | True | False | False | False |
| sq_hard_entrypoint_settings | 6 | 6 | 0 | — | — | True | True | False | False |
| sq_hard_fallback_callers | 6 | 4 | 4 | 0 | 0.8 | True | False | False | False |
| sq_hard_impl_not_tests | 6 | 6 | 0 | — | — | True | True | False | False |
