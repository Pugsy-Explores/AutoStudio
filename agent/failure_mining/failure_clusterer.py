"""Cluster FailureRecords by failure_type, step_type, retry_strategy, prompt_tokens, and (failure_type, step_type)."""

from agent.failure_mining.failure_extractor import FailureRecord


def _bucket_prompt_tokens(tokens: int) -> str:
    """Bucket prompt_tokens into low/medium/high."""
    if tokens <= 1000:
        return "low"
    if tokens <= 5000:
        return "medium"
    return "high"


def cluster_by_failure_type(records: list[FailureRecord]) -> dict[str, list[FailureRecord]]:
    """Group records by failure_type."""
    clusters: dict[str, list[FailureRecord]] = {}
    for r in records:
        ft = r.failure_type or "unknown"
        if ft not in clusters:
            clusters[ft] = []
        clusters[ft].append(r)
    return clusters


def cluster_by_step_type(records: list[FailureRecord]) -> dict[str, list[FailureRecord]]:
    """Group records by step_type."""
    clusters: dict[str, list[FailureRecord]] = {}
    for r in records:
        st = r.step_type or "unknown"
        if st not in clusters:
            clusters[st] = []
        clusters[st].append(r)
    return clusters


def cluster_by_retry_strategy(records: list[FailureRecord]) -> dict[str, list[FailureRecord]]:
    """Group records by retry_strategy."""
    clusters: dict[str, list[FailureRecord]] = {}
    for r in records:
        rs = r.retry_strategy or "none"
        if rs not in clusters:
            clusters[rs] = []
        clusters[rs].append(r)
    return clusters


def cluster_by_prompt_tokens(records: list[FailureRecord]) -> dict[str, list[FailureRecord]]:
    """Group records by prompt_tokens bucket (low/medium/high)."""
    clusters: dict[str, list[FailureRecord]] = {}
    for r in records:
        bucket = _bucket_prompt_tokens(r.prompt_tokens)
        if bucket not in clusters:
            clusters[bucket] = []
        clusters[bucket].append(r)
    return clusters


def cluster_by_failure_type_step_type(
    records: list[FailureRecord],
) -> dict[str, list[FailureRecord]]:
    """Group records by composite key (failure_type, step_type)."""
    clusters: dict[str, list[FailureRecord]] = {}
    for r in records:
        ft = r.failure_type or "unknown"
        st = r.step_type or "unknown"
        key = f"({ft}, {st})"
        if key not in clusters:
            clusters[key] = []
        clusters[key].append(r)
    return clusters


def cluster_all(records: list[FailureRecord]) -> dict[str, dict[str, list[FailureRecord]]]:
    """
    Cluster by all dimensions.
    Returns dict with keys: failure_type, step_type, retry_strategy, prompt_tokens,
    failure_type_step_type.
    """
    return {
        "failure_type": cluster_by_failure_type(records),
        "step_type": cluster_by_step_type(records),
        "retry_strategy": cluster_by_retry_strategy(records),
        "prompt_tokens": cluster_by_prompt_tokens(records),
        "failure_type_step_type": cluster_by_failure_type_step_type(records),
    }


def compute_percentage_stats(
    clusters: dict[str, list[FailureRecord]],
    total: int,
) -> dict[str, float]:
    """Compute percentage of total for each cluster key."""
    if total <= 0:
        return {}
    return {k: len(v) / total for k, v in clusters.items()}
