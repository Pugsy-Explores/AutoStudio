"""Generate human-readable failure analysis report and JSON stats."""

import json
from dataclasses import asdict
from pathlib import Path

from agent.failure_mining.failure_clusterer import (
    cluster_all,
    compute_percentage_stats,
)
from agent.failure_mining.failure_extractor import FailureRecord


def _compute_metrics(records: list[FailureRecord]) -> dict:
    """Compute all report metrics from records."""
    total = len(records)
    success_records = [r for r in records if r.status == "success"]
    failure_records = [r for r in records if r.status == "failure"]
    n_success = len(success_records)
    n_failure = len(failure_records)

    avg_steps_success = (
        sum(r.trajectory_length for r in success_records) / n_success
        if n_success else 0.0
    )
    avg_steps_failure = (
        sum(r.trajectory_length for r in failure_records) / n_failure
        if n_failure else 0.0
    )

    loop_failures = [r for r in failure_records if r.failure_type == "loop_failure"]
    loop_failure_rate = len(loop_failures) / n_failure if n_failure else 0.0

    retrieval_miss = [r for r in failure_records if r.failure_type == "retrieval_miss"]
    retrieval_miss_rate = len(retrieval_miss) / n_failure if n_failure else 0.0

    patch_errors = [
        r for r in failure_records
        if r.failure_type in ("incorrect_patch", "syntax_error_patch")
    ]
    patch_error_rate = len(patch_errors) / n_failure if n_failure else 0.0

    localization_errors = [
        r for r in failure_records
        if r.failure_type == "wrong_file_localization"
    ]
    localization_error_rate = len(localization_errors) / n_failure if n_failure else 0.0

    success_rate = n_success / total if total else 0.0
    avg_attempts = (
        sum(r.attempt + 1 for r in records) / total if total else 0.0
    )

    clusters = cluster_all(records)
    failure_type_clusters = clusters["failure_type"]
    failure_type_pct = compute_percentage_stats(
        failure_type_clusters, total
    )

    return {
        "total_tasks": total,
        "success_count": n_success,
        "failure_count": n_failure,
        "success_rate": success_rate,
        "avg_attempts": avg_attempts,
        "avg_steps_success": round(avg_steps_success, 2),
        "avg_steps_failure": round(avg_steps_failure, 2),
        "loop_failure_rate": round(loop_failure_rate, 4),
        "retrieval_miss_rate": round(retrieval_miss_rate, 4),
        "patch_error_rate": round(patch_error_rate, 4),
        "localization_error_rate": round(localization_error_rate, 4),
        "failure_type_percentages": {
            k: round(v * 100, 2) for k, v in failure_type_pct.items()
        },
        "failure_type_counts": {
            k: len(v) for k, v in failure_type_clusters.items()
        },
    }


def _records_to_serializable(records: list[FailureRecord]) -> list[dict]:
    """Convert FailureRecords to JSON-serializable dicts."""
    return [asdict(r) for r in records]


def generate_report(
    records: list[FailureRecord],
    reports_dir: str | Path,
) -> tuple[Path, Path]:
    """
    Generate reports/failure_analysis.md and reports/failure_stats.json.
    Returns (md_path, json_path).
    """
    reports_dir = Path(reports_dir)
    reports_dir.mkdir(parents=True, exist_ok=True)

    metrics = _compute_metrics(records)
    clusters = cluster_all(records)

    # JSON stats
    json_data = {
        "metrics": metrics,
        "failure_type_clusters": {
            k: len(v) for k, v in clusters["failure_type"].items()
        },
        "failure_type_step_type_clusters": {
            k: len(v) for k, v in clusters["failure_type_step_type"].items()
        },
    }
    json_path = reports_dir / "failure_stats.json"
    with open(json_path, "w", encoding="utf-8") as f:
        json.dump(json_data, f, indent=2)

    # Markdown report
    lines = [
        "# Failure Analysis Report",
        "",
        f"Total tasks: {metrics['total_tasks']}",
        f"Success: {metrics['success_count']}",
        f"Success rate: {metrics['success_rate']:.1%}",
        "",
        "## Trajectory metrics",
        "",
        f"- avg_steps_success: {metrics['avg_steps_success']:.2f}",
        f"- avg_steps_failure: {metrics['avg_steps_failure']:.2f}",
        f"- loop_failure_rate: {metrics['loop_failure_rate']:.2%}",
        f"- avg_attempts: {metrics['avg_attempts']:.2f}",
        "",
        "## Failure rates (among failures)",
        "",
        f"- retrieval_miss_rate: {metrics['retrieval_miss_rate']:.2%}",
        f"- patch_error_rate: {metrics['patch_error_rate']:.2%}",
        f"- localization_error_rate: {metrics['localization_error_rate']:.2%}",
        "",
        "## Top failure patterns",
        "",
    ]

    ft_pct = metrics.get("failure_type_percentages", {})
    for ft, pct in sorted(ft_pct.items(), key=lambda x: -x[1]):
        if pct > 0:
            lines.append(f"- {ft}: {pct}%")

    lines.extend(["", "## (failure_type, step_type) co-occurrence", ""])
    ft_st = clusters["failure_type_step_type"]
    for key, recs in sorted(ft_st.items(), key=lambda x: -len(x[1]))[:15]:
        if len(recs) > 0:
            lines.append(f"- {key}: {len(recs)}")

    md_path = reports_dir / "failure_analysis.md"
    with open(md_path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))

    return md_path, json_path
