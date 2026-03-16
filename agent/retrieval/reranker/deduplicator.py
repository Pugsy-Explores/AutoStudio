"""Candidate deduplication before reranking.

Removes candidates with identical snippet content, preserving the original
list order so early-stage retrieval rank is not disturbed.
"""

from __future__ import annotations

import hashlib


def _snippet_hash(snippet: str) -> str:
    return hashlib.sha256(snippet.encode("utf-8", errors="replace")).hexdigest()


def deduplicate_candidates(candidates: list[dict]) -> list[dict]:
    """Return a deduplicated copy of candidates, keyed by snippet hash.

    First occurrence of each unique snippet is kept. Original order is
    preserved so retriever rank signals remain intact for score fusion.
    """
    seen: set[str] = set()
    result: list[dict] = []
    for c in candidates:
        h = _snippet_hash(c.get("snippet") or "")
        if h not in seen:
            seen.add(h)
            result.append(c)
    return result
