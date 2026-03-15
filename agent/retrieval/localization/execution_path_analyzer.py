"""Execution path analyzer: reconstruct forward/backward call chains from anchor symbol."""

import logging
from pathlib import Path

from config.repo_graph_config import SYMBOL_GRAPH_DIR, INDEX_SQLITE
from config.retrieval_config import MAX_EXECUTION_PATHS, MAX_GRAPH_DEPTH
from repo_graph.graph_query import find_symbol
from repo_graph.graph_storage import GraphStorage

logger = logging.getLogger(__name__)

CALL_EDGE_TYPES = ("calls", "call_graph")


def _node_to_path_item(node: dict) -> dict:
    """Convert storage node to path item dict."""
    return {
        "name": node.get("name", ""),
        "file": node.get("file", ""),
        "line": node.get("start_line"),
        "type": node.get("type", ""),
    }


def _dfs_forward(
    storage: GraphStorage,
    node_id: int,
    depth: int,
    path: list[dict],
    visited: set[int],
    paths: list[dict],
    max_paths: int,
) -> None:
    """DFS following outgoing calls edges to build forward execution chains."""
    if len(paths) >= max_paths:
        return
    if depth <= 0:
        return
    neighbors = storage.get_neighbors(node_id, direction="out", edge_types=list(CALL_EDGE_TYPES))
    if not neighbors:
        if len(path) > 1:
            paths.append({"direction": "forward", "path": list(path)})
        return
    for n in neighbors:
        nid = n.get("id")
        if nid is None or nid in visited:
            continue
        visited.add(nid)
        item = _node_to_path_item(n)
        path.append(item)
        if len(path) > 1:
            paths.append({"direction": "forward", "path": list(path)})
        if len(paths) >= max_paths:
            path.pop()
            visited.discard(nid)
            return
        _dfs_forward(storage, nid, depth - 1, path, visited, paths, max_paths)
        path.pop()
        visited.discard(nid)


def _dfs_backward(
    storage: GraphStorage,
    node_id: int,
    depth: int,
    path: list[dict],
    visited: set[int],
    paths: list[dict],
    max_paths: int,
) -> None:
    """DFS following incoming calls edges to build backward (caller) chains."""
    if len(paths) >= max_paths:
        return
    if depth <= 0:
        return
    neighbors = storage.get_neighbors(node_id, direction="in", edge_types=list(CALL_EDGE_TYPES))
    if not neighbors:
        if len(path) > 1:
            paths.append({"direction": "backward", "path": list(path)})
        return
    for n in neighbors:
        nid = n.get("id")
        if nid is None or nid in visited:
            continue
        visited.add(nid)
        item = _node_to_path_item(n)
        path.append(item)
        if len(path) > 1:
            paths.append({"direction": "backward", "path": list(path)})
        if len(paths) >= max_paths:
            path.pop()
            visited.discard(nid)
            return
        _dfs_backward(storage, nid, depth - 1, path, visited, paths, max_paths)
        path.pop()
        visited.discard(nid)


def build_execution_paths(
    anchor_symbol: str,
    project_root: str,
    max_paths: int = MAX_EXECUTION_PATHS,
    depth: int = MAX_GRAPH_DEPTH,
) -> list[dict]:
    """
    Reconstruct forward (callees) and backward (callers) execution chains.
    Returns list of {direction: "forward"|"backward", path: [{name, file, line, type}]}.
    Returns [] on missing index.
    """
    root = Path(project_root).resolve()
    db_path = root / SYMBOL_GRAPH_DIR / INDEX_SQLITE
    if not db_path.exists():
        logger.debug("[execution_path_analyzer] no index at %s", db_path)
        return []

    storage = GraphStorage(str(db_path))
    try:
        anchor = find_symbol(anchor_symbol, storage)
        if not anchor:
            logger.debug("[execution_path_analyzer] anchor '%s' not found", anchor_symbol)
            return []

        symbol_id = anchor.get("id")
        if symbol_id is None:
            return []

        paths: list[dict] = []
        half = max(1, max_paths // 2)

        # Forward paths (callees)
        start_item = _node_to_path_item(anchor)
        _dfs_forward(storage, symbol_id, depth, [start_item], {symbol_id}, paths, half)

        # Backward paths (callers)
        _dfs_backward(storage, symbol_id, depth, [start_item], {symbol_id}, paths, max_paths)

        return paths[:max_paths]
    finally:
        storage.close()
