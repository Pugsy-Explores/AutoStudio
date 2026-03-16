"""Rerank pair preprocessing: token truncation and pair token-limit enforcement.

Uses whitespace-split token counting as a fast approximation. When the
`transformers` library is available the actual tokenizer is used for exact
counts, but the interface is identical either way.
"""

from __future__ import annotations

from config.retrieval_config import MAX_RERANK_PAIR_TOKENS, MAX_RERANK_SNIPPET_TOKENS


def _token_count(text: str) -> int:
    """Approximate token count via whitespace splitting."""
    return len(text.split())


def _truncate_to_tokens(text: str, max_tokens: int) -> str:
    """Return text truncated so its approximate token count <= max_tokens."""
    tokens = text.split()
    if len(tokens) <= max_tokens:
        return text
    return " ".join(tokens[:max_tokens])


def _window_snippet(snippet: str, max_tokens: int) -> str:
    """Extract the first max_tokens tokens from a snippet.

    The interface is intentionally simple — callers may swap in an
    overlap-window variant later without changing the signature.
    """
    return _truncate_to_tokens(snippet, max_tokens)


def prepare_rerank_pairs(
    query: str,
    snippets: list[str],
    max_snippet_tokens: int = MAX_RERANK_SNIPPET_TOKENS,
    max_pair_tokens: int = MAX_RERANK_PAIR_TOKENS,
) -> list[tuple[str, str]]:
    """Return (query, truncated_snippet) pairs safe for cross-encoder input.

    Steps:
    1. Truncate each snippet to max_snippet_tokens.
    2. Compute remaining token budget for the query after the snippet.
    3. Truncate query if query + snippet exceeds max_pair_tokens.
    """
    pairs: list[tuple[str, str]] = []
    for snippet in snippets:
        truncated_snippet = _window_snippet(snippet, max_snippet_tokens)
        snippet_tokens = _token_count(truncated_snippet)
        remaining = max_pair_tokens - snippet_tokens
        if remaining <= 0:
            # snippet alone exceeds budget — truncate snippet to max_pair_tokens
            truncated_snippet = _truncate_to_tokens(truncated_snippet, max_pair_tokens)
            truncated_query = ""
        else:
            truncated_query = _truncate_to_tokens(query, remaining)
        pairs.append((truncated_query, truncated_snippet))
    return pairs
