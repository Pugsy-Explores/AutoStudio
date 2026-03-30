"""
Symbol expansion using repository symbol graph.
Anchor symbol -> expand neighbors depth=2 -> fetch symbol bodies -> rank -> prune to top K.
"""

import logging
import os
from pathlib import Path

from agent.retrieval.context_pruner import prune_context
from agent.retrieval.retrieval_expander import normalize_file_path
from agent.retrieval.result_contract import RETRIEVAL_RESULT_TYPE_SYMBOL_BODY
from agent.tools import read_symbol_body
from config.repo_graph_config import INDEX_SQLITE, SYMBOL_GRAPH_DIR
from config.retrieval_config import (
    DEFAULT_MAX_CHARS,
    MAX_CONTEXT_SNIPPETS,
    MAX_SYMBOLS,
    RETRIEVAL_GRAPH_EXPANSION_DEPTH,
    RETRIEVAL_GRAPH_MAX_NODES,
    RETRIEVAL_MAX_SYMBOL_EXPANSIONS,
)

logger = logging.getLogger(__name__)

# Backward compatibility alias
MAX_SNIPPETS = MAX_CONTEXT_SNIPPETS


def _resolve_path(path: str, project_root: str | None) -> str:
    """Resolve path to absolute; use project_root when path is relative."""
    if not path:
        return path
    p = Path(path)
    if not p.is_absolute() and project_root:
        p = Path(project_root) / path
    return str(p.resolve())


def expand_from_anchors(
    anchors: list[dict],
    query: str,
    project_root: str | None = None,
    max_symbols: int = MAX_SYMBOLS,
    max_snippets: int = MAX_CONTEXT_SNIPPETS,
    graph_telemetry_out: dict | None = None,
) -> list[dict]:
    """
    Expand anchor symbols via repository symbol graph.
    Algorithm: anchor -> expand neighbors depth=2 -> fetch symbol bodies -> rank -> prune to top K.

    Args:
        anchors: List of {file, symbol, line} from search results.
        query: Search query for ranking.
        project_root: Repository root for path resolution and index lookup.
        max_symbols: Cap on expanded nodes before fetching bodies.
        max_snippets: Cap on final output snippets.

    Returns:
        List of {file, symbol, snippet, line_range?} sorted by relevance, length <= max_snippets.
        Returns [] when no graph index exists or no anchor has a resolvable symbol.
    """
    if not anchors or not query:
        return []

    root = Path(project_root or os.environ.get("SERENA_PROJECT_DIR") or os.getcwd()).resolve()
    index_path = root / SYMBOL_GRAPH_DIR / INDEX_SQLITE
    if not index_path.is_file():
        logger.debug("[symbol_expander] no index at %s", index_path)
        return []

    try:
        from repo_graph.graph_query import expand_symbol_dependencies, find_symbol
        from repo_graph.graph_storage import GraphStorage
    except ImportError:
        logger.debug("[symbol_expander] repo_graph not available")
        return []

    storage = GraphStorage(str(index_path))
    try:
        # Find first anchor with a resolvable symbol
        node = None
        for a in anchors:
            if not isinstance(a, dict):
                continue
            symbol_name = (a.get("symbol") or "").strip()
            if not symbol_name:
                continue
            node = find_symbol(symbol_name, storage)
            if node:
                logger.info("[symbol_expander] anchor symbol=%s", symbol_name)
                break

        if not node:
            logger.debug("[symbol_expander] no anchor symbol found in graph")
            return []

        symbol_id = node.get("id")
        if symbol_id is None:
            return []

        expanded, telemetry = expand_symbol_dependencies(
            symbol_id,
            storage,
            depth=RETRIEVAL_GRAPH_EXPANSION_DEPTH,
            max_nodes=RETRIEVAL_GRAPH_MAX_NODES,
            max_symbol_expansions=RETRIEVAL_MAX_SYMBOL_EXPANSIONS,
        )
        if graph_telemetry_out is not None:
            graph_telemetry_out.update(telemetry)
        expanded = expanded[:max_symbols]
        logger.info(
            "[symbol_expander] expanded %d nodes (depth=%d)",
            len(expanded),
            RETRIEVAL_GRAPH_EXPANSION_DEPTH,
        )

        candidates: list[dict] = []
        for n in expanded:
            file_path = normalize_file_path(n.get("file", ""))
            if not file_path:
                continue
            resolved = _resolve_path(file_path, str(root))
            name = n.get("name", "")
            start_line = n.get("start_line")
            end_line = n.get("end_line")
            line = start_line if start_line is not None else 0

            body = read_symbol_body(name or resolved, resolved, line=line if line else None)
            body_from_source = bool(body and str(body).strip())
            if not body:
                body = (n.get("docstring") or name or "")[:500]

            cand: dict = {
                "file": resolved,
                "symbol": name,
                "snippet": body,
                "line": line,
                "line_range": [start_line, end_line] if start_line is not None and end_line is not None else None,
                "type": "symbol",
                "candidate_kind": "symbol",
            }
            if body_from_source:
                cand["retrieval_result_type"] = RETRIEVAL_RESULT_TYPE_SYMBOL_BODY
                cand["implementation_body_present"] = True
            candidates.append(cand)

        if not candidates:
            return []

        # Task 5: Single ranking pass only. Cross-encoder reranker runs later in run_retrieval_pipeline.
        final = prune_context(
            candidates, max_snippets=max_snippets, max_chars=DEFAULT_MAX_CHARS
        )
        logger.info("[symbol_expander] pruned to %d snippets", len(final))
        return final
    finally:
        storage.close()
