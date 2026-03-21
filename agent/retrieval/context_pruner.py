"""Context pruner: limit ranked context by snippets count and char budget, deduplicate."""

import logging

from agent.retrieval.reranker.deduplicator import retrieval_row_identity_key
from config.retrieval_config import (
    DEFAULT_MAX_CHARS,
    DEFAULT_MAX_SNIPPETS,
    MAX_CONTEXT_SNIPPETS,
)

logger = logging.getLogger(__name__)

_KIND_RANK = {
    "symbol": 0,
    "region": 1,
    "file": 2,
    "reference": 3,
    "localization": 4,
}


def _kind_order(c: dict) -> int:
    k = (c.get("candidate_kind") or "").strip().lower()
    return _KIND_RANK.get(k, 50)


def prune_context(
    ranked_context: list[dict],
    max_snippets: int = DEFAULT_MAX_SNIPPETS,
    max_chars: int = DEFAULT_MAX_CHARS,
) -> list[dict]:
    """
    Prune ranked context: keep top snippets, respect limits, prefer symbol over file, deduplicate.
    - Stop when max_snippets or max_chars reached
    - Prefer symbol snippets over region over file (stable sort by kind then original index)
    - Deduplicate by full row identity (aligned with deduplicate_candidates) so metadata-rich rows are not collapsed.
    """
    if not ranked_context:
        return []
    indexed = [(i, c) for i, c in enumerate(ranked_context) if isinstance(c, dict)]
    indexed.sort(key=lambda t: (_kind_order(t[1]), t[0]))
    ordered = [t[1] for t in indexed]
    seen: set[str] = set()
    result: list[dict] = []
    total_chars = 0
    for c in ordered:
        if len(result) >= max_snippets:
            break
        key = retrieval_row_identity_key(c)
        if key in seen:
            continue
        snippet = c.get("snippet") or ""
        snip_len = len(snippet)
        remaining = max_chars - total_chars
        if snip_len > remaining:
            if remaining < 80:
                if c.get("implementation_body_present") is True:
                    logger.warning(
                        "[context_pruner] char budget skips row (file=%s); trying smaller rows",
                        c.get("file"),
                    )
                continue
            snippet = snippet[:remaining]
            snip_len = len(snippet)
        seen.add(key)
        row = dict(c)
        row["snippet"] = snippet
        result.append(row)
        total_chars += snip_len
    logger.info("[search_budget] pruned to %d snippets (max %d)", len(result), max_snippets)
    return result
