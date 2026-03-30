"""Metric keys and aggregation for tiered eval reports."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

# All keys averaged per-task in aggregate_metrics (missing → 0.0).
METRIC_KEYS = (
    "decision_accuracy",
    "retrieval_recall",
    "synthesis_correctness",
    "validation_effectiveness",
    "loop_efficiency",
    "state_progress_score",
    "validation_gain",
)


@dataclass
class EvalMetrics:
    """Aggregated scores in [0, 1] unless noted."""

    decision_accuracy: float = 0.0
    retrieval_recall: float = 0.0
    synthesis_correctness: float = 0.0
    validation_effectiveness: float = 0.0
    loop_efficiency: float = 0.0
    state_progress_score: float = 0.0
    """Did iteration improve agent state: findings, open questions, confidence."""
    validation_gain: float = 0.0
    """Did the post-validation loop improve answer completeness / quality vs pre-validation."""
    n_tasks: int = 0
    by_tier: dict[int, dict[str, float]] = field(default_factory=dict)
    by_module: dict[str, dict[str, float]] = field(default_factory=dict)


def aggregate_metrics(rows: list[dict[str, Any]]) -> EvalMetrics:
    """
    Aggregate per-task score dicts (each from score_task) into EvalMetrics.

    Each row should include tier, module, and METRIC_KEYS when applicable.
    Missing metrics default to 0 for that task's contribution.
    """
    keys = METRIC_KEYS
    out = EvalMetrics()
    if not rows:
        return out

    sums = {k: 0.0 for k in keys}
    tier_sums: dict[int, dict[str, float]] = {}
    tier_counts: dict[int, int] = {}
    mod_sums: dict[str, dict[str, float]] = {}
    mod_counts: dict[str, int] = {}

    for r in rows:
        out.n_tasks += 1
        tier = int(r.get("tier", 0))
        mod = str(r.get("module", "unknown"))
        tier_counts[tier] = tier_counts.get(tier, 0) + 1
        mod_counts[mod] = mod_counts.get(mod, 0) + 1

        for k in keys:
            v = float(r.get(k, 0.0) or 0.0)
            sums[k] += v
            tier_sums.setdefault(tier, {x: 0.0 for x in keys})
            tier_sums[tier][k] += v
            mod_sums.setdefault(mod, {x: 0.0 for x in keys})
            mod_sums[mod][k] += v

    n = float(out.n_tasks)
    for k in keys:
        setattr(out, k, sums[k] / n)

    for t, c in tier_counts.items():
        if c <= 0:
            continue
        out.by_tier[t] = {kk: tier_sums[t][kk] / c for kk in keys}

    for m, c in mod_counts.items():
        if c <= 0:
            continue
        out.by_module[m] = {kk: mod_sums[m][kk] / c for kk in keys}

    return out


__all__ = ["EvalMetrics", "METRIC_KEYS", "aggregate_metrics"]
