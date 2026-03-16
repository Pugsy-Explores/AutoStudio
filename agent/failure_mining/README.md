# Failure Mining Subsystem

Trajectory-scoped failure analysis: extracts failure records from stored trajectories, clusters by failure type and dimensions, and generates root-cause reports. Used for CI guardrails and improvement prioritization.

## Purpose

Analyzes failed agent runs to:
1. Extract structured FailureRecords from trajectory JSON
2. Detect loop failures and hallucinated symbols
3. Cluster by failure_type, step_type, retry_strategy, etc.
4. Generate root-cause reports
5. Optional LLM relabel of unknown failure types

## Architecture

| Module | Purpose |
|--------|---------|
| trajectory_loader | Reads trajectory JSON from `.agent_memory/` |
| failure_extractor | Converts trajectories to FailureRecords; loop/hallucination detection |
| failure_clusterer | Multi-dimensional clustering; percentage stats |
| failure_judge | LLM re-classification of unknown records |
| root_cause_report | Assembles failure analysis report |
| failure_taxonomy | Canonical list of 11 failure types |

## Key Classes

- `extract_records(trajectories, project_root)` — returns list of FailureRecord
- `cluster_all(records)` — returns clustered groups
- `relabel_unknown_records(records)` — LLM relabel

## Failure Taxonomy

retrieval_miss, wrong_file_localization, incorrect_patch, syntax_error_patch, test_failure, tool_error, timeout, hallucinated_api, premature_completion, hallucinated_symbol, loop_failure

## Usage

```bash
python scripts/run_failure_mining.py --tasks 300
python scripts/run_failure_mining.py --skip-run   # analyze existing trajectories only
python scripts/run_failure_mining.py --use-judge   # LLM relabel unknown
```

Output: `reports/failure_analysis.md`, `reports/failure_stats.json`

## CI Guardrails

When `reports/failure_stats.json` exists, prompt CI checks:
- retrieval_miss_rate < 40%
- patch_error_rate < 25%

## See Also

- [Docs/FAILURE_MINING.md](../../Docs/FAILURE_MINING.md) — full documentation
- [dev/roadmap/phase_16_failure_mining.md](../../dev/roadmap/phase_16_failure_mining.md)
