"""Vector adapter: search_by_embedding → list[dict] in legacy format.

search_by_embedding returns {results: [{file, symbol, line, snippet}], query} | None.
Chroma distances are not surfaced in the current API; rank_in_source used instead.

Multi-query / daemon batch: ``retrieve_v2`` calls this once per query. Batched
daemon vector search is used by ``retrieve_v2_multi`` via ``vector_retriever.search_batch``
(``POST /retrieve/vector/batch`` when remote-first).
"""

from __future__ import annotations

import logging

logger = logging.getLogger(__name__)


def fetch_vector(
    query: str,
    project_root: str,
    top_k: int = 10,
) -> tuple[list[dict], list[str]]:
    """Return (rows, warnings). rows shape: {file, symbol, line, snippet, source, metadata}."""
    try:
        from agent.retrieval.vector_retriever import search_by_embedding  # noqa: PLC0415

        out = search_by_embedding(query, project_root=project_root, top_k=top_k)
        if out is None:
            return [], ["vector_unavailable"]

        raw = out.get("results") or []
        results: list[dict] = []
        for i, r in enumerate(raw):
            results.append({
                "file": r.get("file") or "",
                "symbol": r.get("symbol") or "",
                "line": r.get("line") or 0,
                "snippet": (r.get("snippet") or "")[:500],
                "source": "vector",
                "metadata": {
                    "rank_in_source": i,
                    "raw_score": None,
                    "source_specific": {},
                },
            })

        logger.debug("[adapter.vector] query=%r → %d rows", query, len(results))
        return results, []

    except Exception as exc:
        logger.warning("[adapter.vector] error: %s", exc)
        return [], [f"vector_error:{type(exc).__name__}:{exc}"]
