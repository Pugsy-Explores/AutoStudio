"""Graph lookup primitive for retrieval_pipeline_v2.

CONTRACT:
  - Passes query string DIRECTLY to storage layer (exact match, then LIKE).
  - NO NL extraction (no _FILLER_WORDS, no CamelCase/snake_case decomposition).
  - NO graph expansion (call graph_query.expand_neighbors separately if needed).
  - Returns ordered list of raw node dicts in match order.

This function is a pure lookup — it does not interpret the query.
The caller decides what string to pass. That separation is enforced here by design.
"""

from __future__ import annotations

import logging
import os
from pathlib import Path

from config.repo_graph_config import INDEX_SQLITE, SYMBOL_GRAPH_DIR

logger = logging.getLogger(__name__)


def graph_lookup(
    query: str,
    project_root: str | None = None,
    limit: int = 15,
) -> tuple[list[dict], list[str]]:
    """Deterministic symbol lookup against the SQLite graph index.

    Args:
        query: raw string passed directly to storage — no interpretation.
        project_root: repository root; falls back to env then cwd.
        limit: max nodes to return.

    Returns:
        (nodes, warnings)
        nodes: list of raw storage dicts {id, name, type, file, start_line,
               end_line, docstring, signature}.
               Empty when index missing or query yields no match.
        warnings: non-empty only on errors (index absent, import failure).

    Lookup order (both deterministic):
        1. Exact name match via get_symbol_by_name (single row, fast).
        2. Substring match via get_symbols_like (LIKE %query%, up to limit).
    """
    if not query or not query.strip():
        return [], []

    root = Path(
        project_root or os.environ.get("SERENA_PROJECT_DIR") or os.getcwd()
    ).resolve()
    index_path = root / SYMBOL_GRAPH_DIR / INDEX_SQLITE

    if not index_path.is_file():
        logger.debug("[graph_lookup] no index at %s", index_path)
        return [], [f"graph_no_index:{index_path}"]

    try:
        from repo_graph.graph_storage import GraphStorage  # noqa: PLC0415
    except (ImportError, RecursionError) as exc:
        return [], [f"graph_import_error:{exc}"]

    storage = GraphStorage(str(index_path))
    try:
        q = query.strip()

        # 1. Exact match — tag with _exact_match=True so adapter can boost.
        node = storage.get_symbol_by_name(q)
        if node:
            row = dict(node)
            row["_exact_match"] = True
            nodes = [row]
        else:
            # 2. Substring match — deterministic SQL LIKE, no NL interpretation.
            raw = storage.get_symbols_like(q, limit=limit)
            nodes = [{**n, "_exact_match": False} for n in raw]

        logger.debug("[graph_lookup] query=%r matched=%d exact=%s", q, len(nodes), bool(node))
        return nodes[:limit], []

    except Exception as exc:
        logger.warning("[graph_lookup] error: %s", exc)
        return [], [f"graph_error:{type(exc).__name__}:{exc}"]
    finally:
        storage.close()
