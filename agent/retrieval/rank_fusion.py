"""Reciprocal Rank Fusion (RRF) for combining multiple retrieval rankings.

RRF avoids score-scale problems between systems (BM25, vector, graph)
by using rank positions instead of raw scores.
"""

from __future__ import annotations

import logging
from collections import defaultdict

logger = logging.getLogger(__name__)

RRF_K = 60  # RRF constant; higher k reduces impact of lower ranks


def _result_key(r: dict) -> tuple[str, str, int]:
    """Deduplication key: (file, symbol, line)."""
    file_path = (r.get("file") or r.get("path") or "").strip()
    symbol = (r.get("symbol") or "").strip()
    line = r.get("line")
    line_int = int(line) if line is not None and isinstance(line, (int, float)) else 0
    return (file_path, symbol, line_int)


def reciprocal_rank_fusion(
    result_lists: list[list[dict]],
    k: int = RRF_K,
    top_n: int = 100,
) -> list[dict]:
    """Merge multiple ranked result lists using RRF.

    RRF score for doc d: sum over all lists of 1 / (k + rank(d in list))

    Args:
        result_lists: List of ranked result lists (e.g. [bm25_results, vector_results, graph_results])
        k: RRF constant (default 60)
        top_n: Maximum number of results to return (default 100)

    Returns:
        Merged list of dicts, deduplicated and sorted by RRF score descending.
    """
    scores: defaultdict[tuple[str, str, int], float] = defaultdict(float)
    first_seen: dict[tuple[str, str, int], dict] = {}

    for lst in result_lists:
        if not lst:
            continue
        for rank, r in enumerate(lst):
            if not r or not isinstance(r, dict):
                continue
            key = _result_key(r)
            rrf_score = 1.0 / (k + rank + 1)
            scores[key] += rrf_score
            if key not in first_seen:
                first_seen[key] = r

    # Sort by RRF score descending, then by original order for ties
    sorted_keys = sorted(scores.keys(), key=lambda k: (-scores[k], k))
    result = [first_seen[k] for k in sorted_keys[:top_n]]
    logger.debug("[rank_fusion] merged %d lists -> %d unique results", len(result_lists), len(result))
    return result
