"""Graph adapter: graph_lookup → list[dict] in legacy format.

Calls graph_lookup (no NL extraction, no expansion).
Builds snippet from docstring → signature → name (first non-empty).
"""

from __future__ import annotations

import logging

from agent.retrieval.graph_lookup import graph_lookup

logger = logging.getLogger(__name__)


def fetch_graph(
    query: str,
    project_root: str,
    top_k: int = 15,
) -> tuple[list[dict], list[str]]:
    """Return (rows, warnings). rows shape: {file, symbol, line, snippet, source, metadata}."""
    nodes, warnings = graph_lookup(query, project_root=project_root, limit=top_k)

    results: list[dict] = []
    for i, n in enumerate(nodes):
        file_path = (n.get("file") or "").strip()
        if not file_path:
            continue

        # Snippet: prefer docstring, then signature, then name
        snippet = (
            n.get("docstring")
            or n.get("signature")
            or n.get("name")
            or ""
        )[:500]

        results.append({
            "file": file_path,
            "symbol": n.get("name") or "",
            "line": n.get("start_line") or 0,
            "snippet": snippet,
            "source": "graph",
            "metadata": {
                "rank_in_source": i,
                "raw_score": None,
                # exact_graph_match=True → pipeline injects this row at rank 0 after RRF.
                # Safe: symbol name match is ground-truth signal (migration plan §boost).
                "exact_graph_match": bool(n.get("_exact_match")),
                "source_specific": {
                    "node_id": n.get("id"),
                    "node_type": n.get("type"),
                },
            },
        })

    logger.debug("[adapter.graph] query=%r → %d rows, %d warnings", query, len(results), len(warnings))
    return results, warnings
