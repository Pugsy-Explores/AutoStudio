"""Rerank pair preprocessing: token truncation aligned to MiniLM max_length."""

from __future__ import annotations

from agent.retrieval.reranker.constants import MAX_RERANK_PAIR_TOKENS, MAX_RERANK_SNIPPET_TOKENS


def _token_count(text: str) -> int:
    return len(text.split())


def _truncate_to_tokens(text: str, max_tokens: int) -> str:
    tokens = text.split()
    if len(tokens) <= max_tokens:
        return text
    return " ".join(tokens[:max_tokens])


def _window_snippet(snippet: str, max_tokens: int) -> str:
    return _truncate_to_tokens(snippet, max_tokens)


def prepare_rerank_pairs(
    query: str,
    snippets: list[str],
    max_snippet_tokens: int = MAX_RERANK_SNIPPET_TOKENS,
    max_pair_tokens: int = MAX_RERANK_PAIR_TOKENS,
) -> list[tuple[str, str]]:
    """Return (query, truncated_snippet) pairs for cross-encoder input."""
    pairs: list[tuple[str, str]] = []
    for snippet in snippets:
        truncated_snippet = _window_snippet(snippet, max_snippet_tokens)
        snippet_tokens = _token_count(truncated_snippet)
        remaining = max_pair_tokens - snippet_tokens
        if remaining <= 0:
            truncated_snippet = _truncate_to_tokens(truncated_snippet, max_pair_tokens)
            truncated_query = ""
        else:
            truncated_query = _truncate_to_tokens(query, remaining)
        pairs.append((truncated_query, truncated_snippet))
    return pairs
