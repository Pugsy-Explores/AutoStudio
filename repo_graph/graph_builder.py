"""Build symbol graph from indexed symbols and dependency edges."""

import logging
from pathlib import Path

from repo_graph.graph_storage import GraphStorage

logger = logging.getLogger(__name__)

# Max unresolved edge names to log (bounded diagnostics)
_MAX_SAMPLE_UNRESOLVED = 5


def _resolve_symbol_to_id(name: str, name_to_id: dict[str, int]) -> int | None:
    """
    Resolve edge symbol name to node id. Tries: exact, short name, module.symbol.
    Deterministic; no nondeterministic behavior.
    """
    if not name or not name.strip():
        return None
    name = name.strip()
    # 1. Exact match
    nid = name_to_id.get(name)
    if nid is not None:
        return nid
    # 2. Short name (last dotted segment)
    short = name.split(".")[-1] if "." in name else name
    nid = name_to_id.get(short)
    if nid is not None:
        return nid
    # 3. Try reversing: if edge has "module.symbol", we might have "symbol" in symbols
    #    (already tried short)
    # 4. Try "module.symbol" when we have "symbol" - build qualified from file
    #    (handled by name_to_id having both from symbol extractor)
    return None


def build_graph(symbols: list[dict], edges: list[dict], output_path: str) -> None:
    """
    Build graph from symbols and edges; write to SQLite.
    symbols: [{symbol_name, symbol_type, file, start_line, end_line, docstring}]
    edges: [{source_symbol, target_symbol, relation_type}]
    """
    storage = GraphStorage(output_path)
    storage._connect()  # Ensure schema exists even when symbols is empty
    name_to_id: dict[str, int] = {}

    # Add all symbol nodes; build comprehensive name index
    for s in symbols:
        node_id = storage.add_node(s)
        name = s.get("symbol_name", "")
        if name:
            name_to_id[name] = node_id
            short = name.split(".")[-1] if "." in name else name
            if short not in name_to_id:
                name_to_id[short] = node_id
            # Also add module.symbol when symbol has file (align with dependency extractor)
            file_path = s.get("file", "")
            if file_path:
                module = Path(file_path).stem
                if module and module != "__init__":
                    qualified = f"{module}.{short}" if short != name else name
                    if qualified not in name_to_id:
                        name_to_id[qualified] = node_id

    # Add edges (resolve names to ids)
    edge_count = 0
    unresolved_pairs: list[tuple[str, str]] = []
    for e in edges:
        src_name = e.get("source_symbol", "")
        tgt_name = e.get("target_symbol", "")
        rel = e.get("relation_type", "references")
        src_id = _resolve_symbol_to_id(src_name, name_to_id)
        tgt_id = _resolve_symbol_to_id(tgt_name, name_to_id)
        if src_id and tgt_id and src_id != tgt_id:
            storage.add_edge(src_id, tgt_id, rel)
            edge_count += 1
        elif src_name or tgt_name:
            unresolved_pairs.append((src_name or "?", tgt_name or "?"))

    logger.info("[graph_builder] nodes=%d edges=%d (resolved=%d unresolved=%d)", len(symbols), len(edges), edge_count, len(unresolved_pairs))
    if len(symbols) == 0:
        logger.warning("[graph_builder] no nodes added")
    if edge_count == 0 and edges:
        sample = unresolved_pairs[:_MAX_SAMPLE_UNRESOLVED]
        logger.warning(
            "[graph_builder] edges provided but none added (name resolution failed); sample_unresolved=%s",
            sample,
        )
    storage.close()
