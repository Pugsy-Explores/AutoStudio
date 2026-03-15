"""Thin facade over agent/retrieval/context_pruner."""

from agent.prompt_system.context.context_budget_manager import ContextBudget
from agent.retrieval.context_pruner import prune_context as _prune_context


def prune(
    ranked: list[dict],
    budget: ContextBudget | None = None,
    max_snippets: int | None = None,
    max_chars: int | None = None,
) -> list[dict]:
    """
    Prune ranked context by snippet count and char budget.
    Delegates to agent/retrieval/context_pruner.
    If budget is provided, uses budget.allocate() to derive limits when max_snippets/max_chars not set.
    """
    from config.retrieval_config import DEFAULT_MAX_CHARS, DEFAULT_MAX_SNIPPETS

    if max_snippets is None:
        max_snippets = DEFAULT_MAX_SNIPPETS
    if max_chars is None:
        max_chars = DEFAULT_MAX_CHARS
    if budget:
        # ~4 chars per token heuristic
        allocated = budget.allocate()
        max_chars = min(max_chars, allocated * 4)
    return _prune_context(ranked, max_snippets=max_snippets, max_chars=max_chars)
