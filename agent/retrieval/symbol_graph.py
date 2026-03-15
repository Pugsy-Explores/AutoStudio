"""
Repository symbol graph — precomputed dependency retrieval from index.sqlite.

Provides instant lookup of symbol dependencies (calls, imports, referenced_by)
from an index built during repo indexing.
See Docs/REPOSITORY_SYMBOL_GRAPH.md for the full design.
"""

import logging
import os

from repo_graph.graph_storage import GraphStorage

logger = logging.getLogger(__name__)


def get_symbol_dependencies(symbol: str, project_root: str | None = None) -> list[dict]:
    """
    Return precomputed dependencies for a symbol (calls, imports, referenced_by).

    Returns list of dicts compatible with context builder: {file, symbol, snippet, type}.
    When no index exists, returns [] (caller will fall back to find_referencing_symbols).
    """
    if not symbol or not symbol.strip():
        return []
    root = project_root or os.environ.get("SERENA_PROJECT_DIR") or os.getcwd()
    index_path = os.path.join(root, ".symbol_graph", "index.sqlite")
    if not os.path.isfile(index_path):
        logger.debug("[symbol_graph] no index at %s", index_path)
        return []

    storage = GraphStorage(index_path)
    try:
        node = storage.get_symbol_by_name(symbol.strip())
        if not node:
            matches = storage.get_symbols_like(symbol.strip(), limit=1)
            node = matches[0] if matches else None
        if not node:
            return []

        node_id = node["id"]
        outgoing = storage.get_neighbors(node_id, direction="out")
        incoming = storage.get_neighbors(node_id, direction="in")

        results: list[dict] = []
        for n in outgoing:
            results.append({
                "file": n.get("file", ""),
                "symbol": n.get("name", ""),
                "snippet": n.get("docstring") or "",
                "type": "calls",
            })
        for n in incoming:
            results.append({
                "file": n.get("file", ""),
                "symbol": n.get("name", ""),
                "snippet": n.get("docstring") or "",
                "type": "referenced_by",
            })
        return results
    finally:
        storage.close()
