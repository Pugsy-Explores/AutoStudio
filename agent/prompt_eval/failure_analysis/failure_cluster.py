"""Cluster failures by error_type, top_failures."""

from agent.prompt_eval.failure_analysis.failure_logger import FailureRecord
from agent.prompt_eval.failure_analysis.failure_patterns import classify_failure


def cluster_failures(records: list[FailureRecord]) -> dict[str, list[FailureRecord]]:
    """Group records by error_type."""
    clusters: dict[str, list[FailureRecord]] = {}
    for r in records:
        et = r.error_type or classify_failure(r)
        if et not in clusters:
            clusters[et] = []
        clusters[et].append(r)
    return clusters


def top_failures(
    records: list[FailureRecord],
    n: int = 10,
) -> list[FailureRecord]:
    """Return top N most recent failures (by timestamp)."""
    sorted_records = sorted(
        records,
        key=lambda r: r.timestamp or "",
        reverse=True,
    )
    return sorted_records[:n]
