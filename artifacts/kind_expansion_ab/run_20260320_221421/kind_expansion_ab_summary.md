# ENABLE_KIND_AWARE_EXPANSION — offline A/B

**Verdict (Step 1):** `inconclusive`

**Diagnostic:** linked_arch=

| Metric | OFF | ON | Δ (ON − OFF) |
|--------|-----|----|--------------|
| explain_success_rate | 1 | 1 | 0 |
| symbol_body_present_rate | 1 | 1 | 0 |
| impl_bias_ok_rate | 1 | 1 | 0 |
| relation_ok_rate | 1 | 1 | 0 |
| all_kinds_typed_rate | 1 | 1 | 0 |
| average_final_context_count | 6 | 6 | 0 |
| average_final_context_chars | 3992.8333 | 3991.6667 | -1.1667 |
| replanner_trigger_rate | 0 | 0 | 0 |
| average_implementation_body_present_count | 4.3333 | 4.3333 | 0 |
| average_linked_row_count | 1.5 | 1.5 | 0 |
| average_symbol_body_count | 4.3333 | 4.3333 | 0 |
| average_file_header_count | 0 | 0 | 0 |
| average_region_body_count | 0 | 0 | 0 |
| average_test_file_row_count | 0 | 0 | 0 |
| average_impl_file_row_count | 6 | 6 | 0 |
| average_prune_loss_proxy | 14 | 14 | 0 |

## Per-task diffs (retrieval_quality)

| task_id | ctx_off | ctx_on | Δ | linked_off | linked_on | impl_off | impl_on |
|---------|---------|--------|---|------------|-----------|----------|---------|
| sq_2hop_arch | 6 | 6 | 0 | 1 | 2 | 6 | 6 |
| sq_config_settings | 6 | 6 | 0 | 1 | 1 | 6 | 6 |
| sq_entrypoint_arch | 6 | 6 | 0 | 3 | 1 | 6 | 6 |
| sq_fallback_guard | 6 | 6 | 0 | 2 | 1 | 6 | 6 |
| sq_impl_not_tests | 6 | 6 | 0 | 1 | 3 | 6 | 6 |
| sq_symbol_exact | 6 | 6 | 0 | 1 | 1 | 6 | 6 |

- OFF run: `/Users/shang/my_work/AutoStudio/artifacts/kind_expansion_ab/run_20260320_221421/ENABLE_KIND_AWARE_EXPANSION_0`
- ON run: `/Users/shang/my_work/AutoStudio/artifacts/kind_expansion_ab/run_20260320_221421/ENABLE_KIND_AWARE_EXPANSION_1`
- Machine-readable: `/Users/shang/my_work/AutoStudio/artifacts/kind_expansion_ab/run_20260320_221421/kind_expansion_ab_comparison.json`
