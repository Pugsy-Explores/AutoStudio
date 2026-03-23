# ENABLE_LLM_BUNDLE_SELECTOR — offline A/B

**Verdict:** `inconclusive`
**Selector quality verdict:** `promote_candidate`
**Selector decision source:** `model` | **Confidence:** `normal`
**quality verdict is representative**

| Metric | OFF | ON | Δ (ON − OFF) |
|--------|-----|----|--------------|
| task_count | 6 | 6 | — |
| bundle_selector_usage_rate | 0 | 0 | 0 |
| average_selected_id_count | 0 | 0 | 0 |
| average_selected_impl_body_count | 0 | 0 | 0 |
| average_selected_linked_row_count | 0 | 0 | 0 |
| average_selected_test_row_count | 0 | 0 | 0 |
| selected_rows_only_rate | 0 | 0 | 0 |
| replanner_trigger_rate | 0.1667 | 0.1667 | 0 |
| architecture_ok_rate | 0 | 0.1667 | 0.1667 |

## Selector quality
- Impl retention rate: —
- Linked retention rate: —
- Test drift rate: —
- Architecture answer ready rate: 0
- Selector vs baseline context delta: —
- Architecture tasks lost all links rate: 0.3333
- Useful compaction rate: —
- Selector integrity rate: —
- Verdict: `promote_candidate`

## Interpretation
- Selector usage rate: 0
- Avg selected IDs: 0
- Impl-body retention: 0
- Linked-row retention (architecture): 0
- Replanner trigger rate delta: 0

- OFF run: `/Users/shang/my_work/AutoStudio/artifacts/bundle_selector_ab/run_20260320_235916/ENABLE_LLM_BUNDLE_SELECTOR_0`
- ON run: `/Users/shang/my_work/AutoStudio/artifacts/bundle_selector_ab/run_20260320_235916/ENABLE_LLM_BUNDLE_SELECTOR_1`
- Machine-readable: `/Users/shang/my_work/AutoStudio/artifacts/bundle_selector_ab/run_20260320_235916/bundle_selector_ab_comparison.json`

## Selector-hard slice

**Hard-slice verdict:** `inconclusive` | **Quality:** `promote_candidate`

| task_id | ctx_off | ctx_on | sel_on | linked_ret | impl_ret | arch_ready_off | arch_ready_on | replanner_off | replanner_on |
|---------|---------|--------|--------|------------|----------|----------------|---------------|---------------|--------------|
| sq_hard_2hop_arch | 6 | 6 | 0 | — | — | True | False | False | False |
| sq_hard_config_runtime | 0 | 0 | 0 | — | — | False | False | False | False |
| sq_hard_dispatch_executor | 6 | 6 | 0 | — | — | False | False | False | False |
| sq_hard_entrypoint_settings | 0 | 0 | 0 | — | — | False | False | False | False |
| sq_hard_fallback_callers | 6 | 6 | 0 | — | — | False | False | True | True |
| sq_hard_impl_not_tests | 6 | 6 | 0 | — | — | False | False | False | False |
