# ENABLE_KIND_AWARE_EXPANSION — offline A/B

**Verdict (Step 1):** `inconclusive`

**Diagnostic:** linked_arch=

| Metric | OFF | ON | Δ (ON − OFF) |
|--------|-----|----|--------------|
| explain_success_rate | 1 | 1 | 0 |
| symbol_body_present_rate | 1 | 1 | 0 |
| impl_bias_ok_rate | — | — | — |
| relation_ok_rate | 1 | 1 | 0 |
| all_kinds_typed_rate | 1 | 1 | 0 |
| architecture_task_count | 4 | 4 | — |
| architecture_ok_rate | 0.25 | 0.25 | 0 |
| average_final_context_count | 6 | 6 | 0 |
| average_final_context_chars | 3806.5 | 3807 | 0.5 |
| average_distinct_impl_file_count | 2.25 | 2.25 | 0 |
| replanner_trigger_rate | 0 | 0 | 0 |
| average_implementation_body_present_count | 4.75 | 4.75 | 0 |
| average_linked_row_count | 1.25 | 1.25 | 0 |
| average_symbol_body_count | 4.75 | 4.75 | 0 |
| average_file_header_count | 0 | 0 | 0 |
| average_region_body_count | 0 | 0 | 0 |
| average_test_file_row_count | 0 | 0 | 0 |
| average_impl_file_row_count | 6 | 6 | 0 |
| average_prune_loss_proxy | 14 | 14 | 0 |

## Per-task diffs (retrieval_quality)


## Architecture tasks

| task_id | ctx_off | ctx_on | Δ | linked_off | linked_on | impl_off | impl_on | arch_ok_off | arch_ok_on |
|---------|---------|--------|---|------------|-----------|----------|---------|-------------|------------|
| sq_2hop_arch | 6 | 6 | 0 | 1 | 1 | 6 | 6 | False | False |
| sq_config_settings | 6 | 6 | 0 | 1 | 2 | 6 | 6 | False | True |
| sq_entrypoint_arch | 6 | 6 | 0 | 1 | 1 | 6 | 6 | False | False |
| sq_fallback_guard | 6 | 6 | 0 | 2 | 1 | 6 | 6 | True | False |

- OFF run: `/Users/shang/my_work/AutoStudio/artifacts/kind_expansion_ab/run_20260320_222003/ENABLE_KIND_AWARE_EXPANSION_0`
- ON run: `/Users/shang/my_work/AutoStudio/artifacts/kind_expansion_ab/run_20260320_222003/ENABLE_KIND_AWARE_EXPANSION_1`
- Machine-readable: `/Users/shang/my_work/AutoStudio/artifacts/kind_expansion_ab/run_20260320_222003/kind_expansion_ab_comparison.json`
