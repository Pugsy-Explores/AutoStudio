"""Conditional compression: only compress when repo_context_tokens > MAX_REPO_CONTEXT_TOKENS."""

from config.agent_config import MAX_REPO_CONTEXT_TOKENS


def compress(
    ranked: list[dict],
    repo_context_tokens: int,
    max_tokens: int | None = None,
    model_name: str = "default",
) -> tuple[list[dict], float]:
    """
    Conditional compression: skip if repo_context_tokens <= MAX_REPO_CONTEXT_TOKENS.
    Otherwise delegate to agent/repo_intelligence/context_compressor.
    Returns (compressed_list, compression_ratio). Ratio is 1.0 when compression skipped.
    """
    threshold = max_tokens if max_tokens is not None else MAX_REPO_CONTEXT_TOKENS
    if repo_context_tokens <= threshold:
        return (list(ranked), 1.0)
    from agent.repo_intelligence.context_compressor import compress_context as _compress_context

    return _compress_context(ranked, max_tokens=threshold)
