"""Prompt usage metrics dataclass."""

from dataclasses import dataclass, field


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
    # Phase 14 token budgeting telemetry
    prompt_tokens: int = 0
    system_tokens: int = 0
    skills_tokens: int = 0
    repo_context_tokens: int = 0
    history_tokens: int = 0
    retrieved_snippet_tokens: int = 0
    compression_ratio: float = 1.0
    budget_allocations: dict = field(default_factory=dict)
    pruning_triggered: bool = False
    compression_triggered: bool = False
    emergency_truncation_triggered: bool = False
