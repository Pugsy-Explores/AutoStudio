"""Graph-based symbol retrieval: lookup + expansion for SEARCH pipeline."""

import logging
import os
from pathlib import Path

logger = logging.getLogger(__name__)

MAX_RETRIEVED_SYMBOLS = 15
EXPANSION_DEPTH = 2


def retrieve_symbol_context(query: str, project_root: str | None = None) -> dict | None:
    """
    Retrieve symbol context from precomputed graph.
    Pipeline: symbol lookup -> graph expansion (2 hops) -> return results.
    Returns {results: [{file, symbol, line, snippet}], query} or None when no index.
    """
    if not query or not query.strip():
        return {"results": [], "query": query}

    root = Path(project_root or os.environ.get("SERENA_PROJECT_DIR") or os.getcwd()).resolve()
    index_path = root / ".symbol_graph" / "index.sqlite"
    if not index_path.is_file():
        logger.debug("[graph_retriever] no index at %s", index_path)
        return None

    try:
        from repo_graph.graph_query import expand_neighbors, find_symbol
        from repo_graph.graph_storage import GraphStorage
    except ImportError:
        logger.debug("[graph_retriever] repo_graph not available")
        return None

    storage = GraphStorage(str(index_path))
    try:
        node = find_symbol(query.strip(), storage)
        if not node:
            return None

        symbol_id = node.get("id")
        if symbol_id is None:
            return None

        expanded = expand_neighbors(symbol_id, depth=EXPANSION_DEPTH, storage=storage)
        expanded = expanded[:MAX_RETRIEVED_SYMBOLS]

        results = []
        for n in expanded:
            file_path = n.get("file", "")
            name = n.get("name", "")
            line = n.get("start_line") or 0
            snippet = (n.get("docstring") or name or "")[:300]
            results.append({
                "file": file_path,
                "symbol": name,
                "line": line,
                "snippet": snippet,
            })

        logger.info("[graph_retriever] expansion depth=%d nodes=%d", EXPANSION_DEPTH, len(results))
        return {"results": results, "query": query}
    finally:
        storage.close()
