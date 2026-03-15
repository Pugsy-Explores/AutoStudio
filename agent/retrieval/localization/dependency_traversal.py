"""Dependency traversal: BFS over symbol graph to find callers, callees, imports, dependents."""

import logging
from collections import deque
from pathlib import Path

from config.repo_graph_config import SYMBOL_GRAPH_DIR, INDEX_SQLITE
from config.retrieval_config import MAX_DEPENDENCY_NODES, MAX_GRAPH_DEPTH
from repo_graph.graph_query import find_symbol
from repo_graph.graph_storage import GraphStorage

logger = logging.getLogger(__name__)


def traverse_dependencies(
    symbol: str,
    project_root: str,
    depth: int = MAX_GRAPH_DEPTH,
    max_nodes: int = MAX_DEPENDENCY_NODES,
) -> dict:
    """
    Walk dependency graph: callers, callees, imports, dependents.
    Returns {candidate_symbols: list[dict], candidate_files: list[str], node_count: int}.
    Each candidate_symbol has hop_distance added.
    Returns empty dict with empty lists if no index found.
    """
    root = Path(project_root).resolve()
    db_path = root / SYMBOL_GRAPH_DIR / INDEX_SQLITE
    if not db_path.exists():
        logger.debug("[dependency_traversal] no index at %s", db_path)
        return {"candidate_symbols": [], "candidate_files": [], "node_count": 0}

    storage = GraphStorage(str(db_path))
    try:
        anchor = find_symbol(symbol, storage)
        if not anchor:
            logger.debug("[dependency_traversal] anchor '%s' not found", symbol)
            return {"candidate_symbols": [], "candidate_files": [], "node_count": 0}

        symbol_id = anchor.get("id")
        if symbol_id is None:
            return {"candidate_symbols": [], "candidate_files": [], "node_count": 0}

        visited: set[int] = {symbol_id}
        result: list[dict] = []
        result.append({**dict(anchor), "hop_distance": 0})

        queue: deque[tuple[int, int]] = deque([(symbol_id, 0)])
        while queue and len(result) < max_nodes:
            node_id, d = queue.popleft()
            if d >= depth:
                continue
            neighbors = storage.get_neighbors(node_id, direction="both")
            for n in neighbors:
                nid = n.get("id")
                if nid is not None and nid not in visited:
                    visited.add(nid)
                    node_dict = {**dict(n), "hop_distance": d + 1}
                    result.append(node_dict)
                    queue.append((nid, d + 1))
                    if len(result) >= max_nodes:
                        break

        candidate_symbols = result
        candidate_files = list({n.get("file", "") for n in candidate_symbols if n.get("file")})

        return {
            "candidate_symbols": candidate_symbols,
            "candidate_files": candidate_files,
            "node_count": len(candidate_symbols),
        }
    finally:
        storage.close()
