"""Build symbol graph from indexed symbols and dependency edges."""

import logging
from pathlib import Path

from repo_graph.graph_storage import GraphStorage

logger = logging.getLogger(__name__)


def build_graph(symbols: list[dict], edges: list[dict], output_path: str) -> None:
    """
    Build graph from symbols and edges; write to SQLite.
    symbols: [{symbol_name, symbol_type, file, start_line, end_line, docstring}]
    edges: [{source_symbol, target_symbol, relation_type}]
    """
    storage = GraphStorage(output_path)
    storage._connect()  # Ensure schema exists even when symbols is empty
    name_to_id: dict[str, int] = {}

    # Add all symbol nodes
    for s in symbols:
        node_id = storage.add_node(s)
        name = s.get("symbol_name", "")
        if name:
            # Prefer qualified names; last write wins for duplicates
            name_to_id[name] = node_id
            # Also index by short name for partial matches
            short = name.split(".")[-1] if "." in name else name
            if short not in name_to_id:
                name_to_id[short] = node_id

    # Add edges (resolve names to ids)
    edge_count = 0
    for e in edges:
        src_name = e.get("source_symbol", "")
        tgt_name = e.get("target_symbol", "")
        rel = e.get("relation_type", "references")
        src_id = name_to_id.get(src_name) or name_to_id.get(src_name.split(".")[-1] if "." in src_name else src_name)
        tgt_id = name_to_id.get(tgt_name) or name_to_id.get(tgt_name.split(".")[-1] if "." in tgt_name else tgt_name)
        if src_id and tgt_id and src_id != tgt_id:
            storage.add_edge(src_id, tgt_id, rel)
            edge_count += 1

    logger.info("[graph_builder] nodes=%d edges=%d", len(symbols), edge_count)
    if len(symbols) == 0:
        logger.warning("[graph_builder] no nodes added")
    if edge_count == 0 and edges:
        logger.warning("[graph_builder] edges provided but none added (name resolution may have failed)")
    storage.close()
