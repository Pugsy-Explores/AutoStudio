"""Detect architecture-level impact of edits using symbol graph."""

import logging
import os
from pathlib import Path

logger = logging.getLogger(__name__)

INDEX_SQLITE = "index.sqlite"
SYMBOL_GRAPH_DIR = ".symbol_graph"

RISK_LOW = "LOW"
RISK_MEDIUM = "MEDIUM"
RISK_HIGH = "HIGH"

# Thresholds for risk levels
CALLER_THRESHOLD_MEDIUM = 2
CALLER_THRESHOLD_HIGH = 5
AFFECTED_FILES_THRESHOLD_MEDIUM = 2
AFFECTED_FILES_THRESHOLD_HIGH = 4


def detect_change_impact(
    edited_symbols: list[tuple[str, str]],
    project_root: str | None = None,
) -> dict:
    """
    Compute architecture-level impact of edits.
    edited_symbols: list of (file, symbol) pairs.
    Returns {affected_files, affected_symbols, risk_level}.
    """
    root = Path(project_root or os.environ.get("SERENA_PROJECT_DIR", os.getcwd())).resolve()
    index_path = root / SYMBOL_GRAPH_DIR / INDEX_SQLITE

    affected_files: set[str] = set()
    affected_symbols: set[tuple[str, str]] = set()

    for file_path, symbol in edited_symbols:
        affected_files.add(file_path)
        if symbol:
            affected_symbols.add((file_path, symbol))

    if not index_path.exists():
        risk = RISK_LOW if len(affected_files) <= 1 else RISK_MEDIUM
        logger.info("[change_detector] affected_symbols=%d (no index)", len(affected_symbols))
        return {
            "affected_files": sorted(affected_files),
            "affected_symbols": [f"{f}:{s}" for f, s in sorted(affected_symbols)],
            "risk_level": risk,
        }

    try:
        from repo_graph.graph_query import find_symbol
        from repo_graph.graph_storage import GraphStorage

        storage = GraphStorage(str(index_path))
        try:
            for file_path, symbol in edited_symbols:
                if not symbol:
                    continue
                node = find_symbol(symbol, storage)
                if not node:
                    continue
                node_id = node.get("id")
                if node_id is None:
                    continue

                # Incoming: callers
                callers = storage.get_neighbors(node_id, direction="in")
                for n in callers:
                    nfile = n.get("file", "")
                    nname = n.get("name", "")
                    if nfile:
                        affected_files.add(nfile)
                        if nname:
                            affected_symbols.add((nfile, nname))

                # Outgoing: callees (transitive)
                callees = storage.get_neighbors(node_id, direction="out")
                for n in callees:
                    nfile = n.get("file", "")
                    nname = n.get("name", "")
                    if nfile:
                        affected_files.add(nfile)
                        if nname:
                            affected_symbols.add((nfile, nname))

        finally:
            storage.close()
    except ImportError:
        pass

    # Compute risk level
    num_callers = len(affected_symbols) - len(edited_symbols)
    num_files = len(affected_files)

    if num_files >= AFFECTED_FILES_THRESHOLD_HIGH or num_callers >= CALLER_THRESHOLD_HIGH:
        risk = RISK_HIGH
    elif num_files >= AFFECTED_FILES_THRESHOLD_MEDIUM or num_callers >= CALLER_THRESHOLD_MEDIUM:
        risk = RISK_MEDIUM
    else:
        risk = RISK_LOW

    logger.info("[change_detector] affected_symbols=%d", len(affected_symbols))
    return {
        "affected_files": sorted(affected_files),
        "affected_symbols": [f"{f}:{s}" for f, s in sorted(affected_symbols)],
        "risk_level": risk,
    }
