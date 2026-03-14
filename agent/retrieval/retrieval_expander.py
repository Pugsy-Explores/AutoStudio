"""Retrieval expansion: turn search results into follow-up actions (read_file / read_symbol_body). Capped for large repos."""

import logging

logger = logging.getLogger(__name__)

MAX_EXPANDED_FILES = 5


def expand_search_results(results: list[dict]) -> list[dict]:
    """
    Expand search results into a list of actions (read_file or read_symbol_body).
    Input: list of items with at least "file", optionally "symbol".
    Output: list of {"file", "symbol", "action"} capped at MAX_EXPANDED_FILES.
    When symbol is present, action is "read_symbol_body"; else "read_file".
    """
    if not results or not isinstance(results, list):
        return []
    expanded = []
    for r in results[:MAX_EXPANDED_FILES]:
        if not isinstance(r, dict):
            continue
        file_path = r.get("file") or r.get("path") or ""
        if not file_path:
            continue
        symbol = r.get("symbol") or ""
        action = "read_symbol_body" if symbol else "read_file"
        line = r.get("line") if isinstance(r.get("line"), (int, float)) else None
        if line is not None:
            line = int(line)
        entry = {"file": file_path, "symbol": symbol, "action": action, "line": line}
        expanded.append(entry)
        logger.info("[retrieval_expand] %s %s", action, file_path)
    return expanded
