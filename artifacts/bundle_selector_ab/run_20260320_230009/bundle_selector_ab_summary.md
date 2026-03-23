# ENABLE_LLM_BUNDLE_SELECTOR — offline A/B

**Verdict:** `canary_worthy`

| Metric | OFF | ON | Δ (ON − OFF) |
|--------|-----|----|--------------|
| task_count | 4 | 4 | — |
| bundle_selector_usage_rate | 0 | 1 | 1 |
| average_selected_id_count | 0 | 4 | 4 |
| average_selected_impl_body_count | 0 | 4 | 4 |
| average_selected_linked_row_count | 0 | 0 | 0 |
| average_selected_test_row_count | 0 | 0 | 0 |
| selected_rows_only_rate | 0 | 1 | 1 |
| replanner_trigger_rate | 0 | 0 | 0 |
| architecture_ok_rate | 0.75 | 0 | -0.75 |

## Interpretation
- Selector usage rate: 1
- Avg selected IDs: 4
- Impl-body retention: 4
- Linked-row retention (architecture): 0
- Replanner trigger rate delta: 0

- OFF run: `/Users/shang/my_work/AutoStudio/artifacts/bundle_selector_ab/run_20260320_230009/ENABLE_LLM_BUNDLE_SELECTOR_0`
- ON run: `/Users/shang/my_work/AutoStudio/artifacts/bundle_selector_ab/run_20260320_230009/ENABLE_LLM_BUNDLE_SELECTOR_1`
- Machine-readable: `/Users/shang/my_work/AutoStudio/artifacts/bundle_selector_ab/run_20260320_230009/bundle_selector_ab_comparison.json`
