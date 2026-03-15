"""Graph query: find symbol, expand neighbors."""

import logging
from collections import deque

from config.repo_graph_config import MAX_EXPANSION_DEPTH
from repo_graph.graph_storage import GraphStorage

logger = logging.getLogger(__name__)


def find_symbol(symbol_name: str, storage: GraphStorage) -> dict | None:
    """Find symbol by exact name, then by substring match."""
    node = storage.get_symbol_by_name(symbol_name)
    if node:
        return dict(node)
    matches = storage.get_symbols_like(symbol_name, limit=1)
    result = matches[0] if matches else None
    if result is None:
        logger.debug("[graph_query] find_symbol('%s') not found", symbol_name)
    return result


def expand_neighbors(
    symbol_id: int,
    depth: int = MAX_EXPANSION_DEPTH,
    storage: GraphStorage | None = None,
) -> list[dict]:
    """
    BFS expansion from symbol_id up to depth hops.
    Returns list of node dicts (including the start node).
    """
    if storage is None or depth < 1:
        logger.debug("[graph_query] expand_neighbors: storage=%s depth=%s", storage is not None, depth)
        return []

    visited: set[int] = {symbol_id}
    result: list[dict] = []
    start = storage.get_symbol(symbol_id)
    if start:
        result.append(dict(start))
    else:
        logger.debug("[graph_query] expand_neighbors: symbol_id=%d not found in storage", symbol_id)

    queue: deque[tuple[int, int]] = deque([(symbol_id, 0)])
    while queue:
        node_id, d = queue.popleft()
        if d >= depth:
            continue
        neighbors = storage.get_neighbors(node_id, direction="both")
        for n in neighbors:
            nid = n.get("id")
            if nid is not None and nid not in visited:
                visited.add(nid)
                result.append(dict(n))
                queue.append((nid, d + 1))

    return result
