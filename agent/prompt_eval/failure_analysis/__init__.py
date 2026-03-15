"""Failure analysis: logger, patterns, clustering."""

from agent.prompt_eval.failure_analysis.failure_cluster import cluster_failures, top_failures
from agent.prompt_eval.failure_analysis.failure_logger import FailureRecord, log_failure
from agent.prompt_eval.failure_analysis.failure_patterns import classify_failure

__all__ = [
    "FailureRecord",
    "log_failure",
    "classify_failure",
    "cluster_failures",
    "top_failures",
]
