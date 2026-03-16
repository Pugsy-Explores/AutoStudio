"""Graph query: find symbol, expand neighbors."""

import logging
from collections import deque
from typing import Any

from config.repo_graph_config import MAX_EXPANSION_DEPTH
from repo_graph.graph_storage import GraphStorage

logger = logging.getLogger(__name__)

# Edge types for dependency helpers
_CALL_EDGE_TYPES = ["calls", "call_graph"]
_IMPORT_EDGE_TYPES = ["imports"]
_REFERENCE_EDGE_TYPES = ["references"]


def get_callers(symbol_id: int, storage: GraphStorage) -> list[dict]:
    """Get nodes that call this symbol (incoming calls)."""
    return storage.get_neighbors(symbol_id, direction="in", edge_types=_CALL_EDGE_TYPES)


def get_callees(symbol_id: int, storage: GraphStorage) -> list[dict]:
    """Get nodes this symbol calls (outgoing calls)."""
    return storage.get_neighbors(symbol_id, direction="out", edge_types=_CALL_EDGE_TYPES)


def get_imports(symbol_id: int, storage: GraphStorage) -> list[dict]:
    """Get nodes this symbol imports (outgoing imports)."""
    return storage.get_neighbors(symbol_id, direction="out", edge_types=_IMPORT_EDGE_TYPES)


def get_referenced_by(symbol_id: int, storage: GraphStorage) -> list[dict]:
    """Get nodes that reference this symbol (incoming references)."""
    return storage.get_neighbors(symbol_id, direction="in", edge_types=_REFERENCE_EDGE_TYPES)


def _get_all_dependency_neighbors(symbol_id: int, storage: GraphStorage) -> list[dict]:
    """Get all dependency neighbors (callers, callees, imports, referenced_by)."""
    seen: set[int] = set()
    result: list[dict] = []
    for neighbor in (
        get_callers(symbol_id, storage)
        + get_callees(symbol_id, storage)
        + get_imports(symbol_id, storage)
        + get_referenced_by(symbol_id, storage)
    ):
        nid = neighbor.get("id")
        if nid is not None and nid not in seen:
            seen.add(nid)
            result.append(neighbor)
    return result


def expand_symbol_dependencies(
    symbol_id: int,
    storage: GraphStorage | None,
    depth: int = 2,
    max_nodes: int = 20,
    max_symbol_expansions: int = 8,
) -> tuple[list[dict], dict[str, Any]]:
    """
    BFS expansion from symbol_id along dependency edges (calls, imports, references).
    Cycle-safe, respects max_nodes and max_symbol_expansions.

    Returns:
        (nodes, telemetry) where telemetry has graph_nodes_expanded, graph_edges_traversed,
        graph_expansion_depth_used.
    """
    telemetry: dict[str, Any] = {
        "graph_nodes_expanded": 0,
        "graph_edges_traversed": 0,
        "graph_expansion_depth_used": 0,
    }
    if storage is None or depth < 1:
        return [], telemetry

    visited: set[int] = {symbol_id}
    result: list[dict] = []
    start = storage.get_symbol(symbol_id)
    if start:
        result.append(dict(start))
    else:
        return [], telemetry

    expansions_this_symbol = 0
    max_depth_reached = 0
    queue: deque[tuple[int, int]] = deque([(symbol_id, 0)])

    while queue:
        node_id, d = queue.popleft()
        if d >= depth:
            continue
        if len(result) >= max_nodes:
            break
        if expansions_this_symbol >= max_symbol_expansions:
            break

        neighbors = _get_all_dependency_neighbors(node_id, storage)
        telemetry["graph_edges_traversed"] += len(neighbors)

        for n in neighbors:
            nid = n.get("id")
            if nid is not None and nid not in visited:
                visited.add(nid)
                result.append(dict(n))
                expansions_this_symbol += 1
                max_depth_reached = max(max_depth_reached, d + 1)
                queue.append((nid, d + 1))
                if len(result) >= max_nodes or expansions_this_symbol >= max_symbol_expansions:
                    break

    telemetry["graph_nodes_expanded"] = len(result)
    telemetry["graph_expansion_depth_used"] = max_depth_reached
    return result, telemetry


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
