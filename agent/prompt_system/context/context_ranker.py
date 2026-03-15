"""Thin facade over agent/retrieval/context_ranker."""

from agent.retrieval.context_ranker import rank_context as _rank_context


def rank_context(query: str, candidates: list[dict]) -> list[dict]:
    """
    Rank candidates by hybrid score (LLM + symbol + filename + reference).
    Delegates to agent/retrieval/context_ranker.rank_context.
    """
    return _rank_context(query, candidates)
