# Phase 16 — Failure Pattern Mining

Trajectory-scoped failure analysis for data-driven improvements to prompts, retrieval, and retry policies.

## Overview

Run 200–300 tasks, collect trajectories, cluster failures, identify top root causes, and feed improvements back into the system. Follows the same trajectory-analysis methodology used in SWE-agent research.

## Architecture

```
Task Runner (dataset_runner)
     ↓
Trajectory Collector (trajectory_loader)
     ↓
Failure Extractor (failure_extractor)
     ↓
Failure Clustering (failure_clusterer)
     ↓
Root Cause Report (root_cause_report)
```

## Modules

| Module | Purpose |
|--------|---------|
| `agent/failure_mining/trajectory_loader.py` | Load all trajectories (success + failure), tag with `status: success \| failure` |
| `agent/failure_mining/failure_extractor.py` | Convert trajectories to FailureRecords; loop detection (≥3 consecutive identical steps); hallucinated symbol detection |
| `agent/failure_mining/failure_clusterer.py` | Cluster by failure_type, step_type, retry_strategy, (failure_type, step_type) |
| `agent/failure_mining/root_cause_report.py` | Generate `reports/failure_analysis.md` and `reports/failure_stats.json` |
| `agent/failure_mining/failure_judge.py` | Optional LLM relabel of unknown failure types via `call_small_model` |

## Failure Taxonomy

- `retrieval_miss`, `wrong_file_localization`, `incorrect_patch`, `syntax_error_patch`
- `test_failure`, `tool_error`, `timeout`, `hallucinated_api`, `premature_completion`
- `hallucinated_symbol` — patch/reasoning references symbol not in repo graph
- `loop_failure` — identical step repeated ≥3 times consecutively

## Usage

```bash
# Full pipeline: run 300 tasks, extract, cluster, report
python scripts/run_failure_mining.py --tasks 300

# Analyze existing trajectories only
python scripts/run_failure_mining.py --skip-run

# LLM-relabel unknown failure types
python scripts/run_failure_mining.py --use-judge
```

## Output

- **reports/failure_analysis.md** — Human-readable report with top failure patterns, (failure_type, step_type) co-occurrence
- **reports/failure_stats.json** — JSON metrics for CI guardrails

## Metrics

- `avg_steps_success`, `avg_steps_failure` — Mean trajectory length by status
- `loop_failure_rate` — % of failures classified as loop_failure
- `retrieval_miss_rate`, `patch_error_rate`, `localization_error_rate`

## CI Integration

`scripts/run_prompt_ci.py` checks `reports/failure_stats.json` when present:

- `retrieval_miss_rate` < 40%
- `patch_error_rate` < 25%

Exit(1) on breach.

## Separation from prompt_eval

`agent/failure_mining/` is **separate** from `agent/prompt_eval/failure_analysis/`. Do not merge. The former is trajectory-scoped (agent behavior at scale); the latter is prompt-quality scoped.

## See Also

- [dev/roadmap/phase_16_failure_mining.md](../dev/roadmap/phase_16_failure_mining.md) — Implementation summary
- [dev/roadmap/failure-pattern-mining.md](../dev/roadmap/failure-pattern-mining.md) — Principal engineer plan
- [dev/evaluation/metrics.md](../dev/evaluation/metrics.md) — Phase 16 metrics section
