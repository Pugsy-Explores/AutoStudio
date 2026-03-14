"""
Repository symbol graph — placeholder for precomputed dependency retrieval.

When implemented, this module will provide instant lookup of symbol dependencies
(calls, imports, referenced_by) from an index built during repo indexing.
See Docs/REPOSITORY_SYMBOL_GRAPH.md for the full design.

Current: stub that returns [] when no index exists.
"""

import logging
import os

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
    # TODO: Phase 2 — load from SQLite, return structured results
    logger.debug("[symbol_graph] index exists but not yet implemented")
    return []
