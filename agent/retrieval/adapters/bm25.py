"""BM25 adapter: search_bm25 → list[dict] in legacy format.

search_bm25 already returns {file, symbol, line, snippet}.
We add 'source' and 'metadata'. Raw BM25 scores are not exposed by the
current API; rank_in_source is used instead.
"""

from __future__ import annotations

import logging

logger = logging.getLogger(__name__)


def fetch_bm25(
    query: str,
    project_root: str,
    top_k: int = 15,
) -> tuple[list[dict], list[str]]:
    """Return (rows, warnings). rows shape: {file, symbol, line, snippet, source, metadata}."""
    try:
        from agent.retrieval.bm25_retriever import search_bm25  # noqa: PLC0415

        raw = search_bm25(query, project_root=project_root, top_k=top_k)
        results: list[dict] = []
        for i, r in enumerate(raw):
            results.append({
                "file": r.get("file") or "",
                "symbol": r.get("symbol") or "",
                "line": r.get("line") or 0,
                "snippet": (r.get("snippet") or "")[:500],
                "source": "bm25",
                "metadata": {
                    "rank_in_source": i,
                    "raw_score": None,
                    "source_specific": {},
                },
            })

        logger.debug("[adapter.bm25] query=%r → %d rows", query, len(results))
        return results, []

    except Exception as exc:
        logger.warning("[adapter.bm25] error: %s", exc)
        return [], [f"bm25_error:{type(exc).__name__}:{exc}"]
