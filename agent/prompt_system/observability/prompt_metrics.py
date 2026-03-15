"""Prompt usage metrics dataclass."""

from dataclasses import dataclass


@dataclass
class PromptUsageMetric:
    """Aggregated metrics for a prompt."""

    prompt_name: str
    version: str
    prompt_usage: int
    success_rate: float
    failure_rate: float
    avg_latency_ms: float
    token_usage: dict[str, int]  # {"avg_input": N, "avg_output": M}
    tool_usage: dict[str, int]
